# -*- coding: utf-8 -*-
"""Test the acquisition stop channels (hardware-free, reserved_arm config).

Covers both triggers of `vlgp.acquisition.stop_signal.StopSignal` as wired into
`manual_acquisition --stop-file`:

  1. stop file created   -> graceful stop, reason mentions the file
  2. stdin closed        -> graceful stop (fail-safe when the controller dies):
     the watcher itself is exercised in-process with a fake stdin, because the
     DSH sandbox forbids this probe from creating a stdin pipe for a child.

Both run outcomes must end with `session.json` on disk and the parseable markers
the web UI reads (`=== ACQ RECORDING STARTED` / `STOPPED` / `DONE`).

Usage:  python scripts/test/probes/probe_stop_signal.py
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'src'))
CONFIG = REPO / 'scripts' / 'config' / 'test_web_acquisition.yaml'
OUT_ROOT = REPO / 'var' / 'test_fixtures' / 'acq_out'
FLAGS = REPO / 'var' / 'test_fixtures' / 'flags'
OK = True


def check(label: str, cond: bool, extra: str = '') -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}",
          flush=True)


def run(name: str, *, stop_file: Path | None, hold_s: float = 4.0) -> tuple[int, str, Path]:
    """Start one recording, stop it via the stop file, return (code, log, dir)."""
    log = REPO / 'var' / 'test_fixtures' / f'{name}.log'
    sess_dir = OUT_ROOT / name
    if sess_dir.exists():
        shutil.rmtree(sess_dir)
    argv = [sys.executable, '-u', '-m', 'vlgp.capture.manual_acquisition',
            '--config', str(CONFIG), '--name', name, '--status-interval', '1.0']
    if stop_file is not None:
        argv += ['--stop-file', str(stop_file)]
    with open(log, 'w', encoding='utf-8') as lf:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=lf,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', cwd=str(REPO))
        time.sleep(hold_s)                       # let it record a few seconds
        if stop_file is not None:
            stop_file.write_text('stop\n', encoding='utf-8')
        code = proc.wait(timeout=90)
    return code, log.read_text(encoding='utf-8', errors='replace'), sess_dir


def test_stdin_eof_watcher() -> None:
    """StopSignal must fire when stdin reaches EOF (controller gone)."""
    from vlgp.acquisition.stop_signal import StopSignal
    real_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO('')          # immediate EOF
        ev = threading.Event()
        sig = StopSignal(watch_stdin=True, event=ev).arm()
        fired = sig.wait(5.0)
        check('stdin EOF triggers stop', fired and ev.is_set(), str(sig.reason))
        check('reason mentions stdin', 'stdin closed' in (sig.reason or ''),
              str(sig.reason))
        sig.join(timeout_s=1.0)
    finally:
        sys.stdin = real_stdin


def main() -> int:
    FLAGS.mkdir(parents=True, exist_ok=True)
    print('=== 1. stop via stop file (no stdin watch) ===')
    flag = FLAGS / 'stop_1.flag'
    flag.unlink(missing_ok=True)
    code, log, sess = run('acq_stopfile', stop_file=flag)
    print(f'  exit={code}')
    for line in log.splitlines():
        if line.startswith('=== ACQ'):
            print('   ', line)
    check('exit code 0', code == 0, str(code))
    check('session.json written', (sess / 'session.json').is_file())
    check('RECORDING STARTED marker', '=== ACQ RECORDING STARTED' in log)
    check('RECORDING STOPPED marker', '=== ACQ RECORDING STOPPED' in log)
    check('DONE marker with dir', f'=== ACQ DONE dir={sess}' in log)
    check('reason = stop file', 'reason=stop file created' in log,
          next((ln for ln in log.splitlines() if 'STOPPED' in ln), ''))
    elapsed = float(log.split('elapsed=')[1].split('s')[0]) if 'elapsed=' in log else 0
    check('recorded > 2 s (did not stop instantly)', elapsed > 2.0, f'{elapsed}s')

    print('=== 2. stdin EOF fail-safe (watcher unit check) ===')
    test_stdin_eof_watcher()

    sess_json = json.loads((sess / 'session.json').read_text(encoding='utf-8'))
    print(f"  session.json keys: {sorted(sess_json)[:8]}")
    check('session.json carries sources',
          'sources' in sess_json or 'source_summaries' in sess_json,
          str(sorted(sess_json)[:6]))

    print('RESULT:', 'PASS' if OK else 'FAIL')
    return 0 if OK else 1


if __name__ == '__main__':
    raise SystemExit(main())

