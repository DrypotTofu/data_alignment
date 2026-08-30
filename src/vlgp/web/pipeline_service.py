"""Background pipeline jobs for the web platform (T15/T17 extension).

Runs the existing CLI tools (verify_alignment / build_srth_dataset /
images_to_video) as subprocesses so the web UI can trigger the same pipeline
steps the operator would run on the command line, with status + log tail
polled by the frontend.

Implementation notes:
- stdout+stderr are redirected to a per-job log FILE (not pipes) — this works
  under sandboxes that forbid piped stdio and keeps the log for inspection.
- Jobs are keyed by an opaque id returned to the client; the registry is
  module-level (single-process deployment, see align_service 三.8 note).
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_LOG_ROOT = Path(__file__).resolve().parents[3] / 'var' / 'web_pipeline'
LOG_TAIL_BYTES = 32768
LOG_TAIL_LINES = 200


class PipelineJobs:
    """Module-level job registry: start / status / log-tail."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def start(self, kind: str, label: str, argv: list[str]) -> dict:
        """Run one CLI module as a background job.

        ``argv`` is the module path followed by its arguments, e.g.
        ['vlgp.alignment.verify_alignment', '--session', 'sessions/x'].
        """
        _LOG_ROOT.mkdir(parents=True, exist_ok=True)
        job_id = f'{kind}-{int(time.time())}-{uuid.uuid4().hex[:6]}'
        log_path = _LOG_ROOT / f'{job_id}.log'
        with self._lock:
            j = {'id': job_id, 'kind': kind, 'label': label, 'status': 'running',
                 'started_at': time.time(), 'elapsed_s': None, 'exit_code': None,
                 'log': str(log_path)}
            self._jobs[job_id] = j
        logf = open(log_path, 'w', encoding='utf-8')
        proc = subprocess.Popen(
            [sys.executable, '-m', *argv],
            stdout=logf, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace')
        threading.Thread(target=self._watch, args=(job_id, proc, logf),
                         daemon=True).start()
        return self.status(job_id)

    def start_sequence(self, kind: str, label: str,
                       argv_list: list[list[str]]) -> dict:
        """Run several CLI invocations sequentially as one job (e.g. the three
        camera preview videos)."""
        _LOG_ROOT.mkdir(parents=True, exist_ok=True)
        job_id = f'{kind}-{int(time.time())}-{uuid.uuid4().hex[:6]}'
        log_path = _LOG_ROOT / f'{job_id}.log'
        with self._lock:
            j = {'id': job_id, 'kind': kind, 'label': label, 'status': 'running',
                 'started_at': time.time(), 'elapsed_s': None, 'exit_code': None,
                 'log': str(log_path)}
            self._jobs[job_id] = j
        logf = open(log_path, 'w', encoding='utf-8')
        threading.Thread(target=self._watch_sequence,
                         args=(job_id, argv_list, logf), daemon=True).start()
        return self.status(job_id)

    def status(self, job_id: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(job_id)
        if not j:
            return None
        return {'id': j['id'], 'kind': j['kind'], 'label': j['label'],
                'status': j['status'], 'started_at': j['started_at'],
                'elapsed_s': j['elapsed_s'], 'exit_code': j['exit_code']}

    def log_tail(self, job_id: str) -> str | None:
        with self._lock:
            j = self._jobs.get(job_id)
        if not j:
            return None
        p = Path(j['log'])
        if not p.exists():
            return ''
        data = p.read_text(encoding='utf-8', errors='replace')
        if len(data) > LOG_TAIL_BYTES:
            data = data[-LOG_TAIL_BYTES:]
        lines = data.splitlines()[-LOG_TAIL_LINES:]
        return '\n'.join(lines)

    # ------------------------------------------------------------------ impl
    def _watch(self, job_id: str, proc: subprocess.Popen, logf) -> None:
        try:
            code = proc.wait()
        finally:
            logf.close()
        self._finish(job_id, code)

    def _watch_sequence(self, job_id: str, argv_list: list[list[str]], logf) -> None:
        code = 0
        try:
            for argv in argv_list:
                logf.write(f'\n=== {argv[0]} {" ".join(argv[1:])} ===\n')
                logf.flush()
                proc = subprocess.Popen(
                    [sys.executable, '-m', *argv],
                    stdout=logf, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace')
                code = proc.wait()
                if code != 0:
                    break
        finally:
            logf.close()
        self._finish(job_id, code)

    def _finish(self, job_id: str, code: int) -> None:
        with self._lock:
            j = self._jobs[job_id]
            j['status'] = 'done' if code == 0 else 'error'
            j['exit_code'] = code
            j['elapsed_s'] = round(time.time() - j['started_at'], 1)


_pipeline = PipelineJobs()
