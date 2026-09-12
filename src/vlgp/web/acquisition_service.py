"""Web-controlled acquisition jobs (T15 extension).

Runs ``vlgp.capture.manual_acquisition`` as a background subprocess — the same
entry point as ``scripts/tools/start_manual_acquisition.bat`` — and controls it
through the tool's non-interactive channel:

    --stop-file <flag>          stop when the flag file exists (graceful)
    --stop-on-stdin-eof         also stop if our stdin pipe closes (fail-safe:
                                a killed web server must not leave the rig
                                recording forever)

A recording must never be killed blindly: the session has to flush its writers
and write ``session.json``. So ``stop()`` creates the flag and waits; ``kill()``
exists only as a documented emergency last resort.

Live status comes from the tool's log markers plus the session folder size, which
gives the operator GB written, MB/s and a running average — the numbers that
decide whether a recording can run to completion.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from vlgp.acquisition.config import read_base_dir
from vlgp.web.camera_service import session_dir_for, suggest_session_name

STOP_WAIT_S = 90.0            # manual_acquisition: session.stop(timeout=30) + joins
START_MARKER_WAIT_S = 40.0    # wait for "RECORDING STARTED" before writing the flag
SIZE_SAMPLE_S = 3.0           # how often the session folder is measured
MARK_SESSION = '=== ACQ SESSION '
MARK_STARTED = '=== ACQ RECORDING STARTED'
MARK_STOPPED = '=== ACQ RECORDING STOPPED'
MARK_DONE = '=== ACQ DONE '
# Failure signatures inside the acquisition log: the session keeps running on
# the remaining sources, so without surfacing these the operator sees
# "recording" while a camera silently contributed zero frames.
FAIL_PATTERNS = (
    ('did not signal ready', '未就绪（相机打不开）'),
    ('Cannot open camera', '打不开相机'),
    ('exited with code', '进程异常退出'),
    ('exited before signalling ready', '未就绪即退出'),
    ('RuntimeError:', '运行错误'),
)


class AcquisitionJob:
    """One recording: process + stop flag + parsed progress."""

    def __init__(self, job_id: str, config: Path, name: str, log_path: Path,
                 stop_file: Path, proc: subprocess.Popen, stdin_failsafe: bool,
                 base_dir: str | None):
        self.id = job_id
        self.config = config
        self.name = name
        self.log_path = log_path
        self.stop_file = stop_file
        self.proc = proc
        self.stdin_failsafe = stdin_failsafe
        self.base_dir = base_dir
        self.session_dir: Path | None = session_dir_for(base_dir, name)
        self.started_at = time.time()
        self.recording_at: float | None = None
        self.stopped_at: float | None = None
        self.exit_code: int | None = None
        self.state = 'starting'
        self.error: str | None = None
        self._last_size = 0
        self._last_size_at: float | None = None
        self._bytes_written = 0
        self._mb_per_s: float | None = None
        self._samples: dict[str, int] = {}
        self._camera_frames: dict[str, int] = {}
        self._warnings: list[str] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._watch, daemon=True,
                         name=f'acq-watch-{job_id}').start()

    # ------------------------------------------------------------------ impl
    def _watch(self) -> None:
        code = self.proc.wait()
        with self._lock:
            self.exit_code = code
            self.stopped_at = self.stopped_at or time.time()
            if self.state != 'error':
                self.state = 'done' if code == 0 else 'error'
                if code != 0 and not self.error:
                    self.error = f'采集进程退出码 {code}（详见日志）'
        self._sample_size(force=True)
        # tidy up the per-job flag; its only purpose was this one recording
        try:
            self.stop_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _sample_size(self, force: bool = False) -> None:
        """Measure the session folder (bytes so far + smoothed MB/s + frames)."""
        now = time.time()
        if not force and self._last_size_at is not None \
                and now - self._last_size_at < SIZE_SAMPLE_S:
            return
        d = self.session_dir
        if d is None or not d.exists():
            return
        total = 0
        cameras: dict[str, int] = {}
        stack = [d]
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            else:
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        # per-camera frame counts: the acquisition's own [recording] counters
        # read 0 for process cameras (the frames are written by the worker), so
        # count the files it has produced — that is the "is the camera alive"
        # signal the operator needs
        try:
            for e in os.scandir(d):
                if e.is_dir(follow_symlinks=False) and e.name.startswith('camera_arm_'):
                    n = 0
                    try:
                        with os.scandir(e.path) as files:
                            for f in files:
                                if f.name.endswith(('.png', '.jpg')):
                                    n += 1
                    except OSError:
                        pass
                    cameras[e.name] = n
        except OSError:
            pass
        with self._lock:
            self._bytes_written = total
            self._camera_frames = cameras
            if self._last_size_at is not None and now > self._last_size_at:
                dt = now - self._last_size_at
                inst = (total - self._last_size) / dt / 1048576
                self._mb_per_s = (inst if self._mb_per_s is None
                                  else 0.6 * self._mb_per_s + 0.4 * inst)
            self._last_size = total
            self._last_size_at = now

    def _log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return ''

    def refresh(self) -> None:
        """Re-read the log markers and (throttled) re-measure the folder."""
        text = self._log_text()
        stops = 0
        samples: dict[str, int] = {}
        warnings: list[str] = []
        with self._lock:
            for line in text.splitlines():
                if line.startswith(MARK_SESSION):
                    rest = line[len(MARK_SESSION):]
                    if ' DIR ' in rest:
                        self.session_dir = Path(rest.rsplit(' DIR ', 1)[1].strip())
                elif line.startswith(MARK_STARTED):
                    if self.recording_at is None:
                        self.recording_at = time.time()
                        if self.state == 'starting':
                            self.state = 'recording'
                elif line.startswith(MARK_STOPPED):
                    stops += 1
                elif line.strip().startswith('[recording]'):
                    # live per-source counters (printed every status interval) —
                    # this is the ground truth that data is actually flowing,
                    # independent of how much the writers have flushed to disk
                    for tok in line.split()[1:]:
                        if '=' in tok:
                            k, _, v = tok.partition('=')
                            try:
                                samples[k] = int(v)
                            except ValueError:
                                continue
                else:
                    for needle, label in FAIL_PATTERNS:
                        if needle in line:
                            msg = f'{label}: {line.strip()[:160]}'
                            if msg not in warnings:
                                warnings.append(msg)
                        if 'Worker ' in line and 'exited with code' in line:
                            pass
            if stops and self.state == 'recording':
                self.state = 'stopping'
            if self.state == 'starting' and self.exit_code is None \
                    and self.recording_at is None and 'No sources enabled' in text:
                self.state = 'error'
                self.error = '配置里没有启用的数据源'
            self._samples = samples
            # keep only the most recent few warnings
            self._warnings = warnings[-4:]
        self._sample_size()

    # ------------------------------------------------------------------- api
    def status(self) -> dict:
        self.refresh()
        with self._lock:
            base = self.recording_at or self.started_at
            end = self.stopped_at if self.state in ('done', 'error') else time.time()
            elapsed = max(0.0, end - base)
            return {
                'id': self.id, 'state': self.state, 'error': self.error,
                'config': str(self.config), 'session': self.name,
                'session_dir': str(self.session_dir) if self.session_dir else None,
                'pid': self.proc.pid, 'exit_code': self.exit_code,
                'elapsed_s': round(elapsed, 1),
                'recording': self.state == 'recording',
                'bytes_written': self._bytes_written,
                'gb_written': round(self._bytes_written / 1073741824, 3),
                'mb_written': round(self._bytes_written / 1048576, 1),
                'camera_frames': dict(self._camera_frames),
                'mb_per_s': (round(self._mb_per_s, 2)
                             if self._mb_per_s is not None else None),
                'gb_per_min': (round(self._mb_per_s * 60 / 1024, 2)
                               if self._mb_per_s is not None else None),
                'samples': dict(self._samples),
                'warnings': list(self._warnings),
                'stdin_failsafe': self.stdin_failsafe,
                'stop_file': str(self.stop_file),
                'log': str(self.log_path),
            }

    def request_stop(self) -> dict:
        """Create the stop flag, then wait for the process to stop.

        The flag is only created once the acquisition has printed its STARTED
        marker: watching starts before that line, so this guarantees the request
        cannot be mistaken for a stale leftover (and an early stop — clicked
        during camera warm-up — still ends the recording as soon as it starts).
        """
        with self._lock:
            if self.state in ('done', 'error'):
                return {'state': self.state, 'waited_s': 0.0,
                        'still_running': False}
            self.state = 'stopping'
        marked = self._wait_for_start_marker(START_MARKER_WAIT_S)
        try:
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.write_text(f'stop {time.time()}\n', encoding='utf-8')
        except OSError as e:
            with self._lock:
                self.error = f'无法写入停止标志 {self.stop_file}: {e}'
            return {'state': self.state, 'error': self.error}
        t0 = time.time()
        while time.time() - t0 < STOP_WAIT_S and self.proc.poll() is None:
            time.sleep(0.3)
        waited = time.time() - t0
        self.refresh()
        if not marked:
            self.refresh()
        return {'state': self.state, 'waited_s': round(waited, 1),
                'still_running': self.proc.poll() is None,
                'started_marker_seen': marked}

    def _wait_for_start_marker(self, timeout_s: float) -> bool:
        """Wait until the log shows that recording actually started."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if MARK_STARTED in self._log_text():
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.2)
        return MARK_STARTED in self._log_text()

    def kill(self) -> dict:
        """Emergency only: kills the process; the session may lack session.json
        and/or the tail of each CSV/PNG stream."""
        if self.proc.poll() is None:
            self.proc.kill()
            with self._lock:
                note = '已强制结束采集进程（数据尾部可能不完整）'
                self.error = f'{self.error}；{note}' if self.error else note
                self.state = 'error'
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
        self.refresh()
        return self.status()


class AcquisitionRegistry:
    """Single-recording-at-a-time registry (the rig has one set of cameras)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._job: AcquisitionJob | None = None

    def current(self) -> AcquisitionJob | None:
        with self._lock:
            return self._job

    def running(self) -> bool:
        j = self.current()
        return bool(j and j.proc.poll() is None)

    def status(self) -> dict:
        """Current recording state, or idle when nothing is recording.

        A finished job is NOT reported as the current state: the panel used to
        open with "已结束" (the previous run) even though nothing had started in
        this session. Its summary is still available as ``last_run`` so the UI can
        show "上次录制: …" separately.
        """
        j = self.current()
        if j is None:
            return {'state': 'idle', 'recording': False, 'last_run': None}
        st = j.status()
        if j.proc.poll() is not None:
            return {'state': 'idle', 'recording': False, 'last_run': st}
        return {**st, 'last_run': None}

    def start(self, config_path: str | Path, name: str | None,
              log_dir: Path, stop_dir: Path) -> dict:
        if self.running():
            return {'busy': True, 'status': self.status()}
        cfg = Path(config_path)
        if not cfg.is_file():
            raise ValueError(f'配置文件不存在: {cfg}')
        session_name = (name or suggest_session_name()).strip()
        if not session_name or any(c in session_name for c in '\\/:*?"<>|'):
            raise ValueError(f'非法会话名: {session_name!r}')

        log_dir.mkdir(parents=True, exist_ok=True)
        stop_dir.mkdir(parents=True, exist_ok=True)
        job_id = f'acq-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid() % 1000:03d}'
        log_path = log_dir / f'{job_id}.log'
        stop_file = stop_dir / f'{job_id}.stop'
        stop_file.unlink(missing_ok=True)

        base_argv = [sys.executable, '-u', '-m', 'vlgp.capture.manual_acquisition',
                     '--config', str(cfg), '--name', session_name,
                     '--stop-file', str(stop_file)]
        logf = open(log_path, 'w', encoding='utf-8')
        # stdin pipe = fail-safe stop channel; some environments (service
        # accounts, sandboxes) cannot create pipes — fall back to no stdin
        # rather than failing the recording, and report which mode is active.
        try:
            proc = subprocess.Popen(base_argv + ['--stop-on-stdin-eof'],
                                    stdin=subprocess.PIPE, stdout=logf,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding='utf-8', errors='replace',
                                    creationflags=_new_process_group())
            stdin_failsafe = True
        except OSError:
            proc = subprocess.Popen(base_argv, stdin=subprocess.DEVNULL,
                                    stdout=logf, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8',
                                    errors='replace',
                                    creationflags=_new_process_group())
            stdin_failsafe = False
        job = AcquisitionJob(job_id, cfg, session_name, log_path, stop_file,
                             proc, stdin_failsafe, read_base_dir(cfg))
        with self._lock:
            self._job = job
        return job.status()


def _new_process_group() -> int:
    """Own process group on Windows, so a Ctrl-C in the server console does not
    kill the recorder mid-session (the stdin-EOF channel stops it instead)."""
    if sys.platform == 'win32':
        return getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    return 0


_acquisition = AcquisitionRegistry()
