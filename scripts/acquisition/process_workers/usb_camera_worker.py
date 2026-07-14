"""Standalone USB camera capture worker process.

This script is spawned by `USBCameraProcessSource`. It runs the camera capture
and disk writing in separate threads so that slow PNG writes do not block the
capture loop, which keeps the inter-frame interval as uniform as possible.

Usage:
    python -m acquisition.process_workers.usb_camera_worker \
        --index 1 --width 640 --height 480 --out-dir ./out \
        --topic camera/wrist_left/image_raw --duration 10

The worker exits when either the duration expires or a file named `.stop`
appears in the output directory.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

import cv2


def _enable_high_resolution_timer() -> None:
    """Request 1 ms timer resolution to make time.sleep() more accurate."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception as e:
        logger.warning('Could not set timer resolution: %s', e)


def _disable_high_resolution_timer() -> None:
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


def _set_process_priority_high() -> None:
    """Raise the worker process priority class (best effort, non-admin)."""
    try:
        kernel32 = ctypes.windll.kernel32
        HIGH_PRIORITY_CLASS = 0x00000080
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), HIGH_PRIORITY_CLASS)
    except Exception as e:
        logger.debug('Could not set process priority: %s', e)


def _set_capture_thread_priority() -> None:
    """Raise the capture thread priority to reduce scheduling jitter."""
    try:
        kernel32 = ctypes.windll.kernel32
        THREAD_PRIORITY_TIME_CRITICAL = 15
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL)
    except Exception as e:
        logger.debug('Could not set thread priority: %s', e)


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from acquisition.timestamp import ns_to_beijing_iso8601

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def resolve_backend(name: str) -> int:
    mapping = {'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF, 'any': cv2.CAP_ANY}
    if name not in mapping:
        raise ValueError(f"Unknown backend: {name}")
    return mapping[name]


def capture_loop(
    cap: cv2.VideoCapture,
    frame_queue: queue.Queue,
    stop_event: threading.Event,
    fps: int,
    ready_path: Path,
) -> None:
    """Read frames from the camera at the requested rate and enqueue them."""
    _set_capture_thread_priority()

    frame_idx = 0

    # Read the first frame before signalling readiness.
    ret, _ = cap.read()
    if not ret:
        logger.error('First frame read failed')
        return
    ready_path.write_text(str(time.time_ns()))
    logger.info('Worker ready: camera is live')

    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            logger.warning('Frame read failed, exiting capture loop')
            break

        arrival_ns = time.time_ns()
        try:
            frame_queue.put((frame_idx, arrival_ns, frame), timeout=0.5)
        except queue.Full:
            logger.warning('Writer queue full, dropping frame %d', frame_idx)
        frame_idx += 1


def writer_loop(
    frame_queue: queue.Queue,
    stop_event: threading.Event,
    out_dir: Path,
    source_name: str,
    image_format: str,
    actual_w: int,
    actual_h: int,
) -> None:
    """Consume frames from the queue and write them to disk."""
    metadata_path = out_dir / 'metadata.csv'
    with open(metadata_path, 'w', newline='', encoding='utf-8-sig') as meta_file:
        writer = csv.writer(meta_file)
        writer.writerow(['index', 'timestamp_ns', 'timestamp_beijing', 'filename', 'width', 'height'])
        written = 0

        while True:
            try:
                frame_idx, arrival_ns, frame = frame_queue.get(timeout=0.1)
            except queue.Empty:
                if stop_event.is_set() and frame_queue.empty():
                    break
                continue

            filename = f'{source_name}_{frame_idx:08d}_{arrival_ns}.{image_format}'
            filepath = out_dir / filename
            if image_format.lower() in ('png', '.png'):
                cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            else:
                cv2.imwrite(str(filepath), frame)

            beijing_str = ns_to_beijing_iso8601(arrival_ns)
            writer.writerow([frame_idx, arrival_ns, beijing_str, filename, actual_w, actual_h])
            written += 1

        logger.info('Writer finished. Wrote %d frames to %s', written, out_dir)


def main() -> None:
    _enable_high_resolution_timer()
    _set_process_priority_high()

    parser = argparse.ArgumentParser(description='USB camera capture worker process')
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--fourcc', type=str, default='MJPG')
    parser.add_argument('--backend', type=str, default='dshow')
    parser.add_argument('--topic', type=str, required=True)
    parser.add_argument('--source-name', type=str, default=None,
                        help='Prefix used in frame filenames (defaults to topic)')
    parser.add_argument('--out-dir', type=str, required=True)
    parser.add_argument('--duration', type=float, default=None,
                        help='If set, stop after N seconds')
    parser.add_argument('--image-format', type=str, default='png')
    parser.add_argument('--stop-file', type=str, default='.stop',
                        help='Filename that signals the worker to stop')
    parser.add_argument('--ready-file', type=str, default='.ready',
                        help='Filename written after the first frame is captured')
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_path = out_dir / args.stop_file
    if stop_path.exists():
        stop_path.unlink()

    source_name = (args.source_name or args.topic).replace('/', '_')
    backend = resolve_backend(args.backend)

    cap = cv2.VideoCapture(args.index, backend)
    if not cap.isOpened():
        logger.error('Cannot open camera index %d with backend %s', args.index, args.backend)
        sys.exit(1)

    # Minimise internal buffering so we get the freshest frame.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    # Try to disable auto exposure; some cameras will then run at a fixed frame rate.
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
    cap.set(cv2.CAP_PROP_EXPOSURE, -6)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if args.fps is not None:
        cap.set(cv2.CAP_PROP_FPS, args.fps)
    if args.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc[:4]))

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    logger.info('Worker opened camera %d at %dx%d (backend=%s)',
                args.index, actual_w, actual_h, args.backend)

    ready_path = out_dir / args.ready_file
    if ready_path.exists():
        ready_path.unlink()

    # Queue holds a few seconds of frames so brief write stalls do not drop data.
    frame_queue: queue.Queue[Any] = queue.Queue(maxsize=max(args.fps * 2, 30))
    stop_event = threading.Event()

    capture_thread = threading.Thread(
        target=capture_loop,
        args=(cap, frame_queue, stop_event, args.fps, ready_path),
        name='worker-capture',
        daemon=True,
    )
    writer_thread = threading.Thread(
        target=writer_loop,
        args=(frame_queue, stop_event, out_dir, source_name, args.image_format, actual_w, actual_h),
        name='worker-writer',
        daemon=True,
    )

    capture_thread.start()
    writer_thread.start()

    start_ns = time.time_ns()
    end_ns = start_ns + int(args.duration * 1e9) if args.duration else None

    try:
        while not stop_event.is_set():
            if end_ns is not None and time.time_ns() >= end_ns:
                logger.info('Duration reached, stopping worker.')
                break
            if stop_path.exists():
                logger.info('Stop file detected, stopping worker.')
                break
            time.sleep(0.05)
    finally:
        stop_event.set()
        capture_thread.join(timeout=2.0)
        # Allow the writer to drain the queue.
        writer_thread.join(timeout=10.0)
        cap.release()
        if stop_path.exists():
            stop_path.unlink()
        if ready_path.exists():
            ready_path.unlink()
        logger.info('Worker finished.')


if __name__ == '__main__':
    main()
