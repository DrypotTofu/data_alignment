"""Graceful stop triggers for a running acquisition session.

A recording must stop *gracefully* (writers flush, ``session.json`` is written),
so a web/script controller cannot simply kill the process. This module provides
two independent, explicitly controlled triggers:

* **stop file** — the recording stops as soon as the file exists. The controller
  creates it (e.g. a web "停止录制" button) and can watch the log for the
  ``RECORDING STOPPED`` marker.
* **stdin EOF** — needed as a fail-safe: a controller that runs the acquisition
  as a subprocess with ``stdin=PIPE`` leaves the pipe open while it is alive, so
  the pipe closing (controller crashed / was killed) means nobody is left to
  stop the recording — the session stops itself instead of recording forever.

Both funnel into one ``threading.Event`` so a runner can keep its existing
``while not stop_event.is_set()`` loop; ``reason`` records which trigger fired.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

STOP_FILE_POLL_S = 0.5
# A flag file older than this is treated as a leftover from a previous run and
# removed when watching starts. Anything fresher is a LIVE stop request: deleting
# it (as an earlier version did unconditionally) silently ignored a stop that
# arrived while the process was still starting up, and the recording then never
# stopped.
STALE_AFTER_S = 60.0


class StopSignal:
    """Watch a flag file and/or stdin EOF; set ``event`` when either fires."""

    def __init__(self, stop_file: str | Path | None = None,
                 watch_stdin: bool = False,
                 poll_s: float = STOP_FILE_POLL_S,
                 event: threading.Event | None = None):
        self.stop_file = Path(stop_file) if stop_file else None
        self.watch_stdin = bool(watch_stdin)
        self.poll_s = float(poll_s)
        self.event = event or threading.Event()
        self.reason: str | None = None
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._started_at = time.time()

    # ------------------------------------------------------------------ api
    def arm(self) -> 'StopSignal':
        """Start watching (idempotent). Removes only a STALE stop file.

        A file created moments ago is a live stop request (the controller may
        have asked to stop while this process was still importing), so it is
        kept and honoured immediately; only an old leftover is cleared.
        """
        if self._threads:
            return self
        if self.stop_file is not None:
            self._clear_if_stale()
            t = threading.Thread(target=self._watch_file, daemon=True,
                                 name='stop-file-watcher')
            t.start()
            self._threads.append(t)
        if self.watch_stdin:
            t = threading.Thread(target=self._watch_stdin, daemon=True,
                                 name='stop-stdin-watcher')
            t.start()
            self._threads.append(t)
        return self

    def _clear_if_stale(self) -> None:
        assert self.stop_file is not None
        try:
            age = time.time() - self.stop_file.stat().st_mtime
        except OSError:
            return
        if age <= STALE_AFTER_S:
            return
        try:
            self.stop_file.unlink()
            print(f'(removed stale stop file {self.stop_file}, '
                  f'{age:.0f}s old)', flush=True)
        except OSError:
            pass

    def triggered(self) -> bool:
        return self.event.is_set()

    def wait(self, timeout_s: float | None = None) -> bool:
        """Block until a trigger fires (or timeout). Returns whether it fired."""
        return self.event.wait(timeout_s)

    def elapsed_s(self) -> float:
        return round(time.time() - self._started_at, 3)

    def join(self, timeout_s: float = 1.0) -> None:
        for t in self._threads:
            t.join(timeout=timeout_s)

    # --------------------------------------------------------------- internals
    def _trigger(self, reason: str) -> None:
        with self._lock:
            if self.reason is None:
                self.reason = reason
                self.event.set()

    def _watch_file(self) -> None:
        assert self.stop_file is not None
        while not self.event.is_set():
            try:
                if self.stop_file.exists():
                    self._trigger(f'stop file created: {self.stop_file}')
                    return
            except OSError:
                pass
            time.sleep(self.poll_s)

    def _watch_stdin(self) -> None:
        """Treat EOF/any input on stdin as a stop request.

        EOF is the interesting case (the controlling process went away); a line
        of input is accepted too, so the classic "press Enter to stop" flow
        keeps working through the same channel.
        """
        try:
            line = sys.stdin.readline()
        except (OSError, ValueError):
            line = ''
        if self.event.is_set():
            return
        self._trigger('stdin closed (controller gone)' if line == ''
                      else 'stdin input received')


def stdin_is_tty() -> bool:
    """Whether an interactive Enter-based flow makes sense."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (OSError, ValueError):
        return False


def new_stop_file(directory: str | Path, tag: str = 'stop') -> Path:
    """A unique, not-yet-existing stop-file path inside ``directory``."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    return d / f'{tag}_{os.getpid()}_{int(time.time() * 1000)}.flag'
