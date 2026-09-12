# -*- coding: utf-8 -*-
"""Isolate why the acquisition's camera worker fails after a preflight/preview.

Reproduces the parent's spawn exactly (same argv, stdout=PIPE, inherited stdin)
and varies only what happened to the device just before:
  A. nothing                      (baseline — the standalone probe works)
  B. a single frame grab first     (what the Web 自检 / 预览 does)
  C. a frame grab, then 3 s pause
  D. an in-process open+release first (cv2 handle kept alive in a variable)

Needs camera access (DSH harness: danger-full-access).

Usage: python scripts/test/probes/probe_worker_after_grab.py [--index 0]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO / 'var' / 'worker_grab_probe'


def grab_frame(index: int, w: int = 1280, h: int = 720) -> str:
    import cv2
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        return 'grab: cannot open'
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        time.sleep(0.6)
        ok, frame = cap.read()
        return f'grab: frame_read={bool(ok)}'
    finally:
        cap.release()


def spawn_worker_like_parent(tag: str, index: int, w: int, h: int,
                             attempts: int = 16) -> dict:
    out = OUT_ROOT / tag
    if out.exists():
        import shutil
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, '-u', '-m',
           'vlgp.acquisition.process_workers.usb_camera_worker',
           '--index', str(index), '--topic', 'camera/arm_2/image_raw',
           '--out-dir', str(out), '--backend', 'dshow', '--image-format', 'png',
           '--stop-file', '.stop', '--ready-file', '.ready',
           '--source-name', 'camera_arm_2', '--clock-offset-ns', '0',
           '--width', str(w), '--height', str(h), '--fps', '60',
           '--target-fps', '30', '--png-compression', '1',
           '--writer-threads', '6', '--fourcc', 'MJPG', '--warmup-s', '1.5',
           '--open-attempts', str(attempts)]
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding='utf-8', errors='replace', cwd=str(REPO))
    ready = False
    while time.time() - t0 < 22:
        if (out / '.ready').exists():
            ready = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    ready_s = time.time() - t0
    frames = len(list(out.glob('*.png')))
    output = ''
    if ready:
        time.sleep(2.0)
        frames = len(list(out.glob('*.png')))
        (out / '.stop').write_text('', encoding='utf-8')
        try:
            output, _ = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        code = proc.returncode
    else:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            output = ''
        code = proc.returncode
        frames = len(list(out.glob('*.png')))
    return {'tag': tag, 'ready': ready, 'ready_s': round(ready_s, 1),
            'exit': code, 'frames': frames,
            'output': (output or '').strip(),
            'log': str(REPO / 'var' / 'worker_grab_probe' / f'{tag}.out')}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', type=int, default=0)
    ap.add_argument('--w', type=int, default=1280)
    ap.add_argument('--h', type=int, default=720)
    args = ap.parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []

    print('--- A. baseline (nothing before) ---', flush=True)
    results.append(spawn_worker_like_parent('A_baseline', args.index, args.w, args.h))

    print('--- B. frame grab immediately before ---', flush=True)
    print('   ', grab_frame(args.index, args.w, args.h), flush=True)
    results.append(spawn_worker_like_parent('B_after_grab', args.index, args.w, args.h))

    print('--- C. frame grab, 3 s pause ---', flush=True)
    print('   ', grab_frame(args.index, args.w, args.h), flush=True)
    time.sleep(3.0)
    results.append(spawn_worker_like_parent('C_grab_pause', args.index, args.w, args.h))

    print('--- D. open+release held in a variable ---', flush=True)
    import cv2
    holder = cv2.VideoCapture(args.index, cv2.CAP_DSHOW)
    opened = holder.isOpened()
    holder.release()
    print(f'    in-process open={opened}, released (object still alive)', flush=True)
    results.append(spawn_worker_like_parent('D_holder', args.index, args.w, args.h))
    del holder

    print('\nsummary:')
    for r in results:
        print(f"  {r['tag']:<14} ready={r['ready']} ({r['ready_s']}s) "
              f"exit={r['exit']} frames={r['frames']}")
        tail = r['output'].splitlines()[-3:] if r['output'] else []
        for line in tail:
            print(f'      | {line[:150]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
