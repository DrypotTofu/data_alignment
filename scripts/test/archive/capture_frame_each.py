"""Capture a single frame from each detected USB camera and save to disk.

This is useful for headless / CLI environments where cv2.imshow windows are not
available. It reports the actual resolution, backend, and frame-arrival time for
each camera.

Usage:
    python test/capture_frame_each.py --out-dir ./camera_test_frames
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2


def detect_cameras(max_index: int = 10) -> list[dict]:
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    cameras = []
    for idx in range(max_index):
        for backend in backends:
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                cameras.append({
                    'index': idx,
                    'backend_code': backend,
                    'backend_name': cap.getBackendName(),
                    'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    'fps': cap.get(cv2.CAP_PROP_FPS),
                })
                cap.release()
                break
            cap.release()
    return cameras


def capture_from_camera(idx: int, backend: int, timeout_s: float = 5.0) -> tuple[bool, any, float]:
    """Open camera and return (success, frame, elapsed_ms)."""
    cap = cv2.VideoCapture(idx, backend)
    if not cap.isOpened():
        return False, None, 0.0

    start = time.time()
    frame = None
    ok = False
    while time.time() - start < timeout_s:
        ret, frame = cap.read()
        if ret and frame is not None and frame.size > 0:
            ok = True
            break
        time.sleep(0.01)

    elapsed_ms = (time.time() - start) * 1000
    cap.release()
    return ok, frame, elapsed_ms


def main():
    parser = argparse.ArgumentParser(description='Capture one frame per USB camera.')
    parser.add_argument('--max-index', type=int, default=10)
    parser.add_argument('--out-dir', type=str, default='./camera_test_frames')
    parser.add_argument('--timeout', type=float, default=5.0)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cameras = detect_cameras(args.max_index)
    print(f'Detected {len(cameras)} camera(s). Saving frames to {out_dir.resolve()}')
    print('-' * 60)

    saved = []
    for cam in cameras:
        idx = cam['index']
        backend_code = cam['backend_code']
        backend_name = cam['backend_name']
        print(f'Camera index {idx} ({backend_name}) ... ', end='', flush=True)
        ok, frame, elapsed_ms = capture_from_camera(idx, backend_code, args.timeout)
        if not ok:
            print(f'FAILED to capture frame after {elapsed_ms:.1f} ms')
            continue

        h, w = frame.shape[:2]
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = f'cam{idx}_{w}x{h}_{timestamp}.png'
        filepath = out_dir / filename
        cv2.imwrite(str(filepath), frame)
        saved.append(filepath)
        print(f'OK {w}x{h} in {elapsed_ms:.1f} ms -> {filename}')

    print('-' * 60)
    print(f'Saved {len(saved)} frame(s).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
