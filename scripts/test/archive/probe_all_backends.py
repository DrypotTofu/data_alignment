"""Probe each camera index with DSHOW and MSMF backends and report results."""
from __future__ import annotations

import time

import cv2


def probe_camera(idx: int, backend_code: int, backend_name: str):
    print(f'  Probing index {idx} with {backend_name} ... ', end='', flush=True)
    cap = cv2.VideoCapture(idx, backend_code)
    if not cap.isOpened():
        print('OPEN FAILED')
        return False

    time.sleep(0.2)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    # Try reading a frame.
    frame = None
    for _ in range(20):
        ret, f = cap.read()
        if ret and f is not None and f.size > 0:
            frame = f
            break
        time.sleep(0.05)

    cap.release()

    if frame is None:
        print(f'OPEN OK {actual_w}x{actual_h} @ {actual_fps:.2f} fps, READ FAILED')
        return False

    brightness = frame.mean()
    nonzero = (frame > 10).sum() / frame.size
    print(f'OK {actual_w}x{actual_h} @ {actual_fps:.2f} fps, brightness={brightness:.1f}, nonzero={nonzero:.3f}')
    return True


def main():
    for idx in range(5):
        print(f'Camera index {idx}:')
        probe_camera(idx, cv2.CAP_DSHOW, 'DSHOW')
        probe_camera(idx, cv2.CAP_MSMF, 'MSMF')
        print()


if __name__ == '__main__':
    raise SystemExit(main())
