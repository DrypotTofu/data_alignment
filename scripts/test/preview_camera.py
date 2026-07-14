"""Real-time preview for any USB/DirectShow camera.

Usage:
    python test/preview_camera.py --index 3 [--width 1280] [--height 720]

Press 'q' to stop.  By default it runs indefinitely; use --duration N to
auto-exit after N seconds.
"""
from __future__ import annotations

import argparse
import time

import cv2


def main():
    parser = argparse.ArgumentParser(description='Preview a USB camera')
    parser.add_argument('--index', type=int, default=3, help='Camera index')
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--duration', type=float, default=0.0,
                        help='Auto-exit after N seconds (0 = run until you press q)')
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f'ERROR: Cannot open camera index {args.index}')
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f'Camera opened at {actual_w}x{actual_h}')

    window_name = f'Camera {args.index} preview - press q to quit'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    start = time.time()
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            print('Frame read failed')
            break

        frame_count += 1
        elapsed = time.time() - start
        fps = frame_count / elapsed if elapsed > 0 else 0
        cv2.putText(frame, f'{actual_w}x{actual_h} {fps:.1f} fps',
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 255, 0), 2)
        cv2.imshow(window_name, frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print('Quit by user')
            break
        if args.duration > 0 and elapsed >= args.duration:
            print(f'Auto-exit after {args.duration}s, {frame_count} frames')
            break

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
