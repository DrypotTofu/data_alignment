# -*- coding: utf-8 -*-
"""Try every OpenCV backend for each camera index (diagnose "can't capture by index").

The acquisition uses DirectShow (`backend: dshow`); when that fails, MSMF or the
default backend sometimes still opens the device, and the difference tells us
whether the camera is missing, held by another process, or simply not
enumerable by DSHOW.

Usage:  python scripts/test/probes/probe_camera_backends.py [--max-index 3]
"""
from __future__ import annotations

import argparse
import time

import cv2

BACKENDS = [('dshow', cv2.CAP_DSHOW), ('msmf', cv2.CAP_MSMF), ('any', cv2.CAP_ANY)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-index', type=int, default=3)
    args = ap.parse_args()
    for name, flag in BACKENDS:
        print(f'--- backend {name} ---')
        for idx in range(args.max_index + 1):
            t0 = time.perf_counter()
            cap = cv2.VideoCapture(idx, flag)
            opened = cap.isOpened()
            detail = ''
            if opened:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS)
                ok, frame = cap.read()
                detail = (f'{w}x{h} @{fps:.1f} frame_read={bool(ok)}'
                          + (f' shape={frame.shape}' if ok and frame is not None else ''))
            cap.release()
            print(f"  index {idx}: opened={opened} {detail}"
                  f" ({time.perf_counter() - t0:.2f}s)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
