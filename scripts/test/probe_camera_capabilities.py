"""Probe a single USB camera for supported resolutions and formats.

Useful for capture cards / HDMI-to-USB adapters that report a bogus default
resolution. Tries a list of common resolutions and reports which ones produce
a valid frame.

Usage:
    python test/probe_camera_capabilities.py --index 0
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2


COMMON_RESOLUTIONS = [
    (1920, 1080),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 480),
    (320, 240),
]

BACKENDS = {
    'dshow': cv2.CAP_DSHOW,
    'msmf': cv2.CAP_MSMF,
    'any': cv2.CAP_ANY,
}


def try_resolution(idx: int, backend: int, width: int, height: int, fourcc: str | None):
    cap = cv2.VideoCapture(idx, backend)
    if not cap.isOpened():
        return None

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))

    # Give the driver a moment to settle.
    time.sleep(0.3)

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    # Read a few frames and pick the first non-empty one.
    frame = None
    for _ in range(10):
        ret, f = cap.read()
        if ret and f is not None and f.size > 0:
            frame = f
            break
        time.sleep(0.05)

    cap.release()

    if frame is None:
        return None

    return {
        'requested': (width, height),
        'actual': (actual_w, actual_h),
        'fps': actual_fps,
        'frame_shape': frame.shape,
        'mean_brightness': float(frame.mean()),
        'nonzero_ratio': float((frame > 10).sum() / frame.size),
    }


def main():
    parser = argparse.ArgumentParser(description='Probe camera capabilities.')
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--save-first-frame', action='store_true',
                        help='Save the first valid frame to disk for visual inspection.')
    parser.add_argument('--out-dir', type=str, default='./camera_test_frames')
    args = parser.parse_args()

    backend = BACKENDS[args.backend]
    print(f'Probing camera index {args.index} with backend {args.backend}')
    print('-' * 70)

    results = []
    for w, h in COMMON_RESOLUTIONS:
        print(f'Trying {w}x{h} ... ', end='', flush=True)
        res = try_resolution(args.index, backend, w, h, None)
        if res is None:
            print('NO FRAME')
            continue
        aw, ah = res['actual']
        bright = res['mean_brightness']
        nonzero = res['nonzero_ratio']
        print(f'OK actual={aw}x{ah} fps={res["fps"]:.2f} brightness={bright:.1f} nonzero={nonzero:.3f}')
        results.append(res)

    if not results:
        print('No working resolution found.')
        return 1

    if args.save_first_frame:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        best = max(results, key=lambda r: r['nonzero_ratio'])
        # Re-open at best resolution and save one frame.
        cap = cv2.VideoCapture(args.index, backend)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, best['requested'][0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, best['requested'][1])
        time.sleep(0.3)
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            filename = f'cam{args.index}_probe_{best["actual"][0]}x{best["actual"][1]}.png'
            cv2.imwrite(str(out_dir / filename), frame)
            print(f'Saved {filename}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
