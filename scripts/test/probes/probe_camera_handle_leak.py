# -*- coding: utf-8 -*-
"""Does an unreleased capture handle in one process block the acquisition worker?

The Web service opens the camera for 扫描/预览/自检. If such a handle is never
released (or the driver keeps the device claimed), the acquisition worker cannot
open index N afterwards — which matches the observed failure: worker prints
nothing at all, then the parent times out after 20 s.

Variant L1 keeps a capture object alive WITHOUT release() and spawns the worker.
Variant L0 is the control (no leak).

Needs camera access (DSH harness: danger-full-access).

Usage: python scripts/test/probes/probe_camera_handle_leak.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / 'var' / 'handle_leak_probe'
CONFIG = REPO / 'scripts' / 'config' / 'arm2_only.yaml'


def record(tag: str, run_s: float = 8.0) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    stop = OUT / f'{tag}.stop'
    stop.unlink(missing_ok=True)
    log = OUT / f'{tag}.log'
    cmd = [sys.executable, '-u', '-m', 'vlgp.capture.manual_acquisition',
           '--config', str(CONFIG), '--name', f'leak_{tag}',
           '--stop-file', str(stop)]
    with open(log, 'w', encoding='utf-8') as lf:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=lf,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace')
        time.sleep(run_s)
        sess = REPO / 'sessions' / f'leak_{tag}' / 'camera_arm_2'
        mid = len(list(sess.glob('*.png'))) if sess.is_dir() else 0
        stop.write_text('stop\n', encoding='utf-8')
        try:
            proc.wait(timeout=45)
        except subprocess.TimeoutExpired:
            proc.kill()
    text = log.read_text(encoding='utf-8', errors='replace')
    pngs = len(list(sess.glob('*.png'))) if sess.is_dir() else 0
    return {'tag': tag, 'ready': 'is ready' in text, 'pngs': pngs,
            'mid': mid, 'not_opening': 'not opening' in text}


def main() -> int:
    results = []
    print('--- L0 control: nothing holding the camera ---', flush=True)
    results.append(record('L0_control'))

    print('--- L1: capture opened, NEVER released, reference kept alive ---', flush=True)
    holder = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    print(f'    opened={holder.isOpened()} (no release() call)', flush=True)
    holder.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    holder.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    time.sleep(0.5)
    ok, _ = holder.read()
    print(f'    frame_read={bool(ok)}; now starting the acquisition with the '
          f'handle still open', flush=True)
    results.append(record('L1_leaked'))
    print('    releasing the leaked handle now', flush=True)
    holder.release()
    del holder

    print('--- L2: after releasing ---', flush=True)
    results.append(record('L2_after_release'))

    print('\nsummary:')
    for r in results:
        print(f"  {r['tag']:<14} ready={r['ready']} pngs={r['pngs']} "
              f"(mid={r['mid']}) not_opening={r['not_opening']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
