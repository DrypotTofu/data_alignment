"""Capture a frame from one camera at a specific resolution and backend.

Usage:
    python test/capture_one_resolution.py --index 1 --width 1024 --height 768 --backend dshow --out-dir ./camera_test_frames
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2


BACKENDS = {
    'dshow': cv2.CAP_DSHOW,
    'msmf': cv2.CAP_MSMF,
    'any': cv2.CAP_ANY,
}


def main():
    parser = argparse.ArgumentParser(description='Capture one frame at a specific resolution.')
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--fps', type=int, default=None)
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--fourcc', type=str, default=None)
    parser.add_argument('--out-dir', type=str, default='./camera_test_frames')
    parser.add_argument('--frames', type=int, default=10, help='Number of frames to read before saving')
    args = parser.parse_args()

    backend = BACKENDS[args.backend]
    cap = cv2.VideoCapture(args.index, backend)
    if not cap.isOpened():
        print(f'ERROR: Cannot open camera {args.index} with {args.backend}')
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if args.fps is not None:
        cap.set(cv2.CAP_PROP_FPS, args.fps)
    if args.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))

    time.sleep(0.3)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f'Opened camera {args.index} at {actual_w}x{actual_h} @ {actual_fps:.2f} fps')

    frame = None
    for i in range(args.frames):
        ret, f = cap.read()
        if ret and f is not None and f.size > 0:
            frame = f
            print(f'  frame {i+1}: shape={f.shape}, mean={f.mean():.1f}')
        else:
            print(f'  frame {i+1}: read failed')
        time.sleep(0.05)

    cap.release()

    if frame is None:
        print('ERROR: No valid frame captured')
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f'cam{args.index}_{args.backend}_{actual_w}x{actual_h}_{time.strftime("%Y%m%d_%H%M%S")}.png'
    filepath = out_dir / filename
    cv2.imwrite(str(filepath), frame)
    print(f'Saved {filepath}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
