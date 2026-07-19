"""Test opening camera 3 (DSHOW) first, then camera 0 (MSMF).

This checks whether the MSMF endoscope blocks DSHOW camera 3 or vice versa.
"""
from __future__ import annotations

import time

import cv2


def try_open(idx: int, backend: int, width: int, height: int, timeout_s: float = 10.0):
    print(f'Opening camera {idx} (backend={backend}) ...')
    cap = cv2.VideoCapture(idx, backend)
    if not cap.isOpened():
        print(f'  FAILED to open')
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    time.sleep(0.3)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f'  OK at {actual_w}x{actual_h}')

    # Read a frame with timeout.
    start = time.time()
    frame = None
    while time.time() - start < timeout_s:
        ret, f = cap.read()
        if ret and f is not None and f.size > 0:
            frame = f
            break
        time.sleep(0.05)

    if frame is None:
        print(f'  FAILED to read frame')
        cap.release()
        return None

    print(f'  Frame read OK, shape={frame.shape}, mean={frame.mean():.1f}')
    return cap


def main():
    cap3 = try_open(3, cv2.CAP_DSHOW, 640, 480)
    if cap3 is None:
        print('Cannot open camera 3 alone')
        return 1

    cap0 = try_open(0, cv2.CAP_MSMF, 1280, 720)
    if cap0 is None:
        print('Cannot open camera 0 after camera 3')
        cap3.release()
        return 1

    # Try reading from both.
    print('Reading 5 frames from camera 3 ...')
    for i in range(5):
        ret, f = cap3.read()
        print(f'  cam3 frame {i}: ret={ret} mean={f.mean() if f is not None else None}')

    print('Reading 5 frames from camera 0 ...')
    for i in range(5):
        ret, f = cap0.read()
        print(f'  cam0 frame {i}: ret={ret} mean={f.mean() if f is not None else None}')

    cap0.release()
    cap3.release()
    print('Released both cameras')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
