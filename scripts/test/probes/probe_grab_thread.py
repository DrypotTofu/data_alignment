# -*- coding: utf-8 -*-
"""Test whether grabbing a frame in a THREAD leaves the DSHOW device busy.

The Web service runs every endpoint in a threadpool thread, and its camera
scan/preview/preflight open the device there. The hypothesis: after such a grab
the device stays busy for other processes (the acquisition worker then blocks in
VideoCapture and never signals ready), while a grab in the main thread releases
cleanly.

Variants (each followed by spawning the acquisition worker like the service):
  T1 grab in the main thread
  T2 grab in a ThreadPoolExecutor thread  (what FastAPI does)
  T3 T2 + del cap + gc.collect()
  T4 no grab at all

Needs camera access (DSH harness: danger-full-access).

Usage: python scripts/test/probes/probe_grab_thread.py
"""
from __future__ import annotations

import gc
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / 'var' / 'grab_thread_probe'
CONFIG = REPO / 'scripts' / 'config' / 'arm2_only.yaml'
EXECUTOR = ThreadPoolExecutor(max_workers=4)


def grab(index: int = 0, w: int = 1280, h: int = 720) -> str:
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        return 'open failed'
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        time.sleep(0.5)
        ok, frame = cap.read()
        return f'frame_read={bool(ok)}'
    finally:
        cap.release()


def grab_holding_ref(index: int = 0, w: int = 1280, h: int = 720):
    """Grab but keep the capture object alive (thread-local leak simulation)."""
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        time.sleep(0.5)
        cap.read()
        cap.release()
    return cap          # returned → caller may drop it; a thread-local would keep it


def record(tag: str, run_s: float = 10.0) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    stop = OUT / f'{tag}.stop'
    stop.unlink(missing_ok=True)
    log = OUT / f'{tag}.log'
    cmd = [sys.executable, '-u', '-m', 'vlgp.capture.manual_acquisition',
           '--config', str(CONFIG), '--name', f'grab_{tag}',
           '--stop-file', str(stop)]
    with open(log, 'w', encoding='utf-8') as lf:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=lf,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace')
        time.sleep(run_s)
        stop.write_text('stop\n', encoding='utf-8')
        try:
            proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            proc.kill()
    text = log.read_text(encoding='utf-8', errors='replace')
    sess = REPO / 'sessions' / f'grab_{tag}' / 'camera_arm_2'
    pngs = len(list(sess.glob('*.png'))) if sess.is_dir() else 0
    return {'tag': tag, 'ready': 'is ready' in text, 'pngs': pngs,
            'not_opening': 'not opening' in text}


def main() -> int:
    results = []
    print('--- T1: grab in MAIN thread ---', flush=True)
    print('   ', grab(), flush=True)
    results.append(record('T1_main'))

    print('--- T2: grab in THREADPOOL thread (FastAPI-like) ---', flush=True)
    print('   ', EXECUTOR.submit(grab).result(), flush=True)
    results.append(record('T2_thread'))

    print('--- T3: THREADPOOL grab, then drop refs + gc ---', flush=True)
    print('   ', EXECUTOR.submit(grab).result(), flush=True)
    gc.collect()
    results.append(record('T3_thread_gc'))

    print('--- T4: thread grab that RETURNS the capture object (leak) ---', flush=True)
    leaked = EXECUTOR.submit(grab_holding_ref).result()
    print(f'    leaked capture alive: {leaked is not None}, isOpened={leaked.isOpened() if leaked is not None else None}',
          flush=True)
    results.append(record('T4_thread_leak'))
    try:
        leaked.release()
    except Exception:      # noqa: BLE001
        pass
    del leaked
    gc.collect()

    print('--- T5: after releasing the leaked object ---', flush=True)
    results.append(record('T5_after_release'))

    print('\nsummary:')
    for r in results:
        print(f"  {r['tag']:<16} ready={r['ready']} pngs={r['pngs']} "
              f"not_opening={r['not_opening']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
