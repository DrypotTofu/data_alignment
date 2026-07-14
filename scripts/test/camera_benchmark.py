"""Benchmark real frame rate and capture latency of a USB camera.

Usage:
    python camera_benchmark.py --index 0 --duration 5
"""
import argparse
import statistics
import time

import cv2


def benchmark(camera_index: int, duration_s: float, backend: int = cv2.CAP_DSHOW) -> None:
    cap = cv2.VideoCapture(camera_index, backend)
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open camera with index {camera_index}')

    # Warm up
    for _ in range(10):
        cap.read()

    intervals = []
    timestamps = []
    start_ns = time.time_ns()
    end_ns = start_ns + int(duration_s * 1e9)

    print(f'Benchmarking camera {camera_index} for {duration_s}s ...')
    prev_ns = start_ns
    while time.time_ns() < end_ns:
        ret, _ = cap.read()
        if not ret:
            break
        now_ns = time.time_ns()
        timestamps.append(now_ns)
        intervals.append((now_ns - prev_ns) / 1e6)  # ms
        prev_ns = now_ns

    cap.release()

    if len(intervals) <= 1:
        print('Not enough frames captured.')
        return

    # Drop first interval (warm-up / initial delay)
    intervals = intervals[1:]
    elapsed_s = (timestamps[-1] - timestamps[0]) / 1e9
    fps = len(intervals) / elapsed_s if elapsed_s > 0 else 0.0

    print('-' * 60)
    print(f'Frames captured: {len(intervals)}')
    print(f'Elapsed time   : {elapsed_s:.3f}s')
    print(f'Average FPS    : {fps:.2f}')
    print(f'Interval mean  : {statistics.mean(intervals):.2f} ms')
    print(f'Interval std   : {statistics.stdev(intervals):.2f} ms')
    print(f'Interval min   : {min(intervals):.2f} ms')
    print(f'Interval max   : {max(intervals):.2f} ms')
    print('-' * 60)


def main():
    parser = argparse.ArgumentParser(description='Benchmark USB camera frame rate.')
    parser.add_argument('--index', type=int, default=0, help='Camera index (default: 0)')
    parser.add_argument('--duration', type=float, default=5.0,
                        help='Benchmark duration in seconds (default: 5)')
    parser.add_argument('--backend', type=str, default='dshow',
                        help='OpenCV backend: dshow, msmf, any (default: dshow)')
    args = parser.parse_args()

    backend_map = {'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF, 'any': cv2.CAP_ANY}
    if args.backend not in backend_map:
        raise ValueError(f"Unknown backend: {args.backend}")
    benchmark(args.index, args.duration, backend_map[args.backend])


if __name__ == '__main__':
    main()
