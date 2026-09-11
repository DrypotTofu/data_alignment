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

import numpy as np
import pandas as pd

_FALLBACK_LOG_ROOT = Path(__file__).resolve().parents[3] / 'var' / 'web_pipeline'
LOG_TAIL_BYTES = 32768
LOG_TAIL_LINES = 200


def _resolve_log_root(log_root: Path | None) -> Path:
    """Job logs live under the ACTIVE data root (``<root>/_web_logs``) so a
    data disk that is nearly full does not also fill the repo disk; fall back
    to the repo ``var/web_pipeline`` when that directory cannot be created
    (read-only archive root)."""
    candidates = ([Path(log_root)] if log_root else []) + [_FALLBACK_LOG_ROOT]
    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            return cand
        except OSError:
            continue
    return candidates[-1]

_TRIM_COLS = {'camera_arm_2': 'anchor_filename',
              'camera_arm_1': 'camera_arm_1_filename',
              'camera_arm_3': 'camera_arm_3_filename'}

# images_to_video keeps frames with ``lo <= timestamp_ns <= hi`` where
# lo/hi are rebuilt from the float seconds as ints, so a float round-trip
# that lands 1 ns short silently DROPS the OUT frame (observed: trim
# [10, 30] exported idx 10..29). Widening the window by 1 us fixes it and is
# still ~30x smaller than one 30 Hz frame interval (33 ms).
_TRIM_EPS_SEC = 1e-6


def video_trim_args(index, name: str, session_dir: Path,
                    trim: dict) -> dict[str, dict]:
    """Per-camera --start-sec/--end-sec for an IN/OUT trim.

    The trim rows live on the 30 Hz alignment grid; preview videos are built
    from each camera's raw metadata timeline. To cut exactly at the annotated
    IN/OUT frames, map each row's anchor/nearest filename back to that
    camera's ``metadata.csv`` timestamp and express it as seconds relative to
    the camera's first frame (what ``images_to_video --start-sec/--end-sec``
    expects). Falls back to grid-relative seconds when the mapping is
    unavailable (legacy alignment tables without the per-camera filename
    columns, or a frame missing from metadata.csv).

    Rows are inclusive on both ends (build_srth_dataset keeps
    ``start_row <= row <= end_row``), so the window is widened by
    ``_TRIM_EPS_SEC`` on each side to survive the ns/float round-trip.

    Returns ``{cam: {'start_sec', 'end_sec', 'source'}}`` where source is
    'exact' (per-camera filename -> timestamp) or 'grid' (30 Hz fallback).
    """
    try:
        df = index.alignment(name)
    except Exception:  # noqa: BLE001 — no alignment table
        df = None
    s0_row, s1_row = int(trim['start_row']), int(trim['end_row'])
    grid_rel = {'start_sec': max(0.0, s0_row / 30.0 - _TRIM_EPS_SEC),
                'end_sec': s1_row / 30.0 + _TRIM_EPS_SEC,
                'source': 'grid'}
    out: dict[str, dict] = {}
    for cam, col in _TRIM_COLS.items():
        exact = None
        if df is not None and col in df.columns:
            try:
                meta = pd.read_csv(session_dir / cam / 'metadata.csv')
                if {'filename', 'timestamp_ns'}.issubset(meta.columns):
                    ts = dict(zip(meta['filename'].astype(str),
                                  meta['timestamp_ns'].astype(np.int64)))
                    t0 = int(meta['timestamp_ns'].iloc[0])

                    def sec_of(row_idx: int):
                        fname = df.iloc[row_idx][col]
                        ns = ts.get(str(fname))
                        return None if ns is None else (int(ns) - t0) / 1e9

                    a = sec_of(s0_row)
                    b = sec_of(s1_row)
                    if a is not None and b is not None and b >= a:
                        exact = {'start_sec': max(0.0, a - _TRIM_EPS_SEC),
                                 'end_sec': b + _TRIM_EPS_SEC,
                                 'source': 'exact'}
            except Exception:  # noqa: BLE001 — fall back to grid-relative
                exact = None
        out[cam] = exact if exact is not None else dict(grid_rel)
    return out


class PipelineJobs:
    """Module-level job registry: start / status / log-tail."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def start(self, kind: str, label: str, argv: list[str],
              log_root: Path | None = None) -> dict:
        """Run one CLI module as a background job.

        ``argv`` is the module path followed by its arguments, e.g.
        ['vlgp.alignment.verify_alignment', '--session', 'sessions/x'].
        """
        log_root = _resolve_log_root(log_root)
        log_root.mkdir(parents=True, exist_ok=True)
        job_id = f'{kind}-{int(time.time())}-{uuid.uuid4().hex[:6]}'
        log_path = log_root / f'{job_id}.log'
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
                       argv_list: list[list[str]],
                       header_lines: list[str] | None = None,
                       log_root: Path | None = None) -> dict:
        """Run several CLI invocations sequentially as one job (e.g. the three
        camera preview videos).

        ``header_lines`` are written verbatim before the first ``=== cmd ===``
        marker so the log explains what was (or was not) exported.
        """
        log_root = _resolve_log_root(log_root)
        log_root.mkdir(parents=True, exist_ok=True)
        job_id = f'{kind}-{int(time.time())}-{uuid.uuid4().hex[:6]}'
        log_path = log_root / f'{job_id}.log'
        with self._lock:
            j = {'id': job_id, 'kind': kind, 'label': label, 'status': 'running',
                 'started_at': time.time(), 'elapsed_s': None, 'exit_code': None,
                 'log': str(log_path)}
            self._jobs[job_id] = j
        logf = open(log_path, 'w', encoding='utf-8')
        threading.Thread(target=self._watch_sequence,
                         args=(job_id, argv_list, logf, header_lines),
                         daemon=True).start()
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

    def running_jobs(self) -> list[dict]:
        """Currently running jobs (id + label) — used to refuse switching the
        data root while a job still writes into the old root."""
        with self._lock:
            jobs = list(self._jobs.values())
        return [{'id': j['id'], 'kind': j['kind'], 'label': j['label']}
                for j in jobs if j['status'] == 'running']

    # ------------------------------------------------------------------ impl
    def _watch(self, job_id: str, proc: subprocess.Popen, logf) -> None:
        try:
            code = proc.wait()
        finally:
            logf.close()
        self._finish(job_id, code)

    def _watch_sequence(self, job_id: str, argv_list: list[list[str]], logf,
                        header_lines: list[str] | None = None) -> None:
        code = 0
        try:
            if header_lines:
                logf.write('\n'.join(header_lines) + '\n')
                logf.flush()
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
