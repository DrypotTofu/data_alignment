# -*- coding: utf-8 -*-
"""Diagnose the acquisition progress counters (GB written / MB per second).

Builds an AcquisitionJob around a dummy process (no cameras, no rig) pointed at a
real session folder, then prints what the Web status endpoint would report. This
isolates "the counter says 0" from "nothing was written".

Usage:  python scripts/test/probes/probe_acq_progress.py [--dir sessions/<session>]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'src'))

from vlgp.web.acquisition_service import AcquisitionJob  # noqa: E402


def dir_stats(path: Path) -> tuple[int, int]:
    """Independent scandir walk (mirrors the private one) for comparison."""
    files = total = 0
    stack = [path]
    while stack:
        with __import__('os').scandir(stack.pop()) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    files += 1
                    total += e.stat(follow_symlinks=False).st_size
    return files, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='sessions/test_20260912_185621')
    args = ap.parse_args()
    target = Path(args.dir)
    if not target.is_absolute():
        target = REPO / target
    print(f'target: {target}  exists={target.is_dir()}')
    if target.is_dir():
        files, total = dir_stats(target)
        print(f'independent walk: {files} files, {total / 1048576:.1f} MB')

    work = REPO / 'var' / 'test_fixtures' / 'progress_probe'
    work.mkdir(parents=True, exist_ok=True)
    log = work / 'fake.log'
    # a log with the real marker format (path contains no spaces here)
    log.write_text(f'=== ACQ SESSION probe DIR {target}\n'
                   '=== ACQ RECORDING STARTED name=probe\n', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job = AcquisitionJob('probe', REPO / 'scripts' / 'config' / 'arm2_only.yaml',
                         'probe', log, work / 'probe.stop', proc, False,
                         str(target.parent))
    print(f'job.session_dir parsed from log marker: {job.session_dir}')
    try:
        for i in range(4):
            st = job.status()
            print(f"  t={i}: state={st['state']} elapsed={st['elapsed_s']}s "
                  f"bytes={st['bytes_written']} gb={st['gb_written']} "
                  f"mb_per_s={st['mb_per_s']}")
            time.sleep(3.2)
    finally:
        proc.kill()
        proc.wait(timeout=10)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
