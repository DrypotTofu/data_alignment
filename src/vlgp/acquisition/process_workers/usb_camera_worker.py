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


from vlgp.acquisition.timestamp import ns_to_beijing_iso8601

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
    ready_path: Path,
    clock_offset_ns: int,
    warmup_s: float = 0.0,
    target_fps: float | None = None,
) -> bool:
    """Read frames from the camera and enqueue them.

    When ``target_fps`` is set, the camera runs at its native (faster) rate and
    frames are kept on a virtual ``1/target_fps`` time grid — the kept frames
    carry their real PC-arrival timestamps. This gives an exact, stable output
    rate even when the driver cannot be configured for that rate directly.

    Returns True if the loop exited normally (stop requested), False if the
    camera died mid-stream (T12-P1-1 fail-fast: the worker then exits non-zero
    so the session notices the missing camera instead of recording silently).
    """
    _set_capture_thread_priority()

    frame_idx = 0
    # Discard frames while auto white balance / exposure are still converging;
    # otherwise the first recorded frames carry a yellow tint.
    warmup_end = time.time() + max(0.0, warmup_s)
    grid_interval = 1.0 / target_fps if target_fps and target_fps > 0 else None
    next_keep = None

    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            logger.error('Camera %s frame read failed — camera may have died', cap.get(cv2.CAP_PROP_POS_MSEC))
            return False

        now = time.time()
        if now < warmup_end:
            continue

        if grid_interval is not None:
            if next_keep is None:
                next_keep = now
            if now < next_keep:
                continue
            # Advance the grid; if we are lagging badly (e.g. writer stall),
            # skip missed slots so we do not burst-keep stale frames.
            while next_keep <= now:
                next_keep += grid_interval

        if frame_idx == 0:
            ready_path.write_text(str(time.time_ns()))
            logger.info('Worker ready: camera is live (warm-up %.1f s done)', warmup_s)

        pc_arrival_ns = time.time_ns()
        timestamp_ns = pc_arrival_ns + clock_offset_ns
        try:
            frame_queue.put((frame_idx, timestamp_ns, pc_arrival_ns, frame), timeout=0.5)
        except queue.Full:
            logger.warning('Writer queue full, dropping frame %d', frame_idx)
        frame_idx += 1

    return True


def writer_loop(
    frame_queue: queue.Queue,
    stop_event: threading.Event,
    out_dir: Path,
    source_name: str,
    image_format: str,
    actual_w: int,
    actual_h: int,
    png_compression: int = 1,
    meta_writer: Any = None,
    meta_lock: threading.Lock | None = None,
) -> None:
    """Consume frames from the queue and write them to disk.

    Multiple writer threads may share the same queue and metadata writer;
    metadata rows may then be slightly out of frame order (each row carries
    the frame index and timestamp, and downstream tools sort by timestamp).
    """
    if meta_writer is None or meta_lock is None:
        raise ValueError('meta_writer and meta_lock are required')

    written = 0
    while True:
        try:
            frame_idx, timestamp_ns, pc_arrival_ns, frame = frame_queue.get(timeout=0.1)
        except queue.Empty:
            if stop_event.is_set() and frame_queue.empty():
                break
            continue

        filename = f'{source_name}_{frame_idx:08d}_{timestamp_ns}.{image_format}'
        filepath = out_dir / filename
        fmt = image_format.lower().lstrip('.')
        if fmt == 'png':
            cv2.imwrite(str(filepath), frame,
                        [cv2.IMWRITE_PNG_COMPRESSION, png_compression])
        elif fmt in ('jpg', 'jpeg'):
            cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        else:
            cv2.imwrite(str(filepath), frame)

        beijing_str = ns_to_beijing_iso8601(timestamp_ns)
        with meta_lock:
            meta_writer.writerow([
                frame_idx, timestamp_ns, pc_arrival_ns,
                beijing_str, filename, actual_w, actual_h,
            ])
        written += 1

    logger.info('Writer thread finished (%d frames, %s)', written, out_dir)


def main() -> None:
    _enable_high_resolution_timer()
    _set_process_priority_high()

    parser = argparse.ArgumentParser(description='USB camera capture worker process')
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--width', type=int, default=None,
                        help='Requested frame width. None = driver default.')
    parser.add_argument('--height', type=int, default=None,
                        help='Requested frame height. None = driver default.')
    parser.add_argument('--fps', type=int, default=None)
    parser.add_argument('--fourcc', type=str, default='MJPG')
    parser.add_argument('--backend', type=str, default='dshow')
    parser.add_argument('--topic', type=str, required=True)
    parser.add_argument('--source-name', type=str, default=None,
                        help='Prefix used in frame filenames (defaults to topic)')
    parser.add_argument('--out-dir', type=str, required=True)
    parser.add_argument('--duration', type=float, default=None,
                        help='If set, stop after N seconds')
    parser.add_argument('--image-format', type=str, default='png')
    parser.add_argument('--png-compression', type=int, default=1,
                        help='PNG compression level 0-9 (0 = fastest/largest, '
                             'lossless at every level). Use 0 for 720p+ at 30fps.')
    parser.add_argument('--writer-threads', type=int, default=1,
                        help='Number of parallel image-writer threads. Use 2 for '
                             'PNG at 720p+ (level-1 PNG encode exceeds one frame '
                             'period at 30 fps)')
    parser.add_argument('--warmup-s', type=float, default=1.5,
                        help='Seconds of frames to discard after opening the camera '
                             '(lets auto white balance / exposure converge)')
    parser.add_argument('--target-fps', type=float, default=None,
                        help='If set, do not touch CAP_PROP_FPS; keep frames on a '
                             'virtual 1/target_fps grid for an exact output rate')
    parser.add_argument('--clock-offset-ns', type=int, default=0,
                        help='Offset added to pc_arrival_ns to align with a master clock')
    parser.add_argument('--stop-file', type=str, default='.stop',
                        help='Filename that signals the worker to stop')
    parser.add_argument('--ready-file', type=str, default='.ready',
                        help='Filename written after the first frame is captured')
    parser.add_argument('--exposure', type=float, default=None,
                        help='Manual exposure (CAP_PROP_EXPOSURE). None = driver '
                             'default.')
    parser.add_argument('--wb-temperature', type=float, default=None,
                        help='Manual white-balance colour temperature in Kelvin '
                             '(CAP_PROP_WB_TEMPERATURE). DSHOW cameras typically '
                             'accept ~2800-6500K; set to the scene illuminant '
                             '(e.g. 6500 for cool-white endoscope LED). None = '
                             'driver default.')
    parser.add_argument('--auto-wb', type=int, default=None,
                        help='CAP_PROP_AUTO_WB: 1 = auto white balance, 0 = manual. '
                             'None = leave driver default.')
    parser.add_argument('--brightness', type=float, default=None,
                        help='Manual brightness (CAP_PROP_BRIGHTNESS, DSHOW range '
                             '-64..64). None = driver default.')
    parser.add_argument('--contrast', type=float, default=None,
                        help='Manual contrast (CAP_PROP_CONTRAST). None = driver default.')
    parser.add_argument('--saturation', type=float, default=None,
                        help='Manual saturation (CAP_PROP_SATURATION). None = driver default.')
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
    if args.exposure is not None:
        cap.set(cv2.CAP_PROP_EXPOSURE, args.exposure)
    # T11: manual white balance. DSHOW wrist cameras lock WB_TEMPERATURE
    # (~4600K default) which mismatches the cool-white endoscope LED, causing a
    # blue cast; setting the temperature to the scene illuminant fixes it.
    # NOTE: property order matters — setting CAP_PROP_AUTO_WB *after*
    # CAP_PROP_WB_TEMPERATURE resets the temperature back to the driver default
    # (verified on these cameras: wb->autowb0 readback=4600, autowb0->wb=6500).
    if args.auto_wb is not None:
        cap.set(cv2.CAP_PROP_AUTO_WB, float(args.auto_wb))
    # Manual temperature and AUTO_WB are mutually exclusive on these DSHOW
    # cameras: setting WB_TEMPERATURE after AUTO_WB=1 switches the driver back
    # to manual (AUTO_WB readback becomes 0). Only apply the temperature when
    # auto WB is off (or unspecified).
    if args.wb_temperature is not None and args.auto_wb != 1:
        cap.set(cv2.CAP_PROP_WB_TEMPERATURE, args.wb_temperature)
    # T11: manual picture controls (brightness/contrast/saturation), optional.
    if args.brightness is not None:
        cap.set(cv2.CAP_PROP_BRIGHTNESS, args.brightness)
    if args.contrast is not None:
        cap.set(cv2.CAP_PROP_CONTRAST, args.contrast)
    if args.saturation is not None:
        cap.set(cv2.CAP_PROP_SATURATION, args.saturation)
    if args.width is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height is not None:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    # CAP_PROP_FPS and target_fps are independent: fps selects the driver's
    # streaming mode (some devices default to a slow mode, e.g. 25 fps, when
    # unset), target_fps decimates that stream to an exact output rate.
    if args.fps is not None:
        cap.set(cv2.CAP_PROP_FPS, args.fps)
    if args.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc[:4]))

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    logger.info('Worker opened camera %d at %dx%d (backend=%s)',
                args.index, actual_w, actual_h, args.backend)
    # T11: log property readbacks to verify manual params actually took effect.
    wb_temp = cap.get(cv2.CAP_PROP_WB_TEMPERATURE)
    exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
    auto_wb = cap.get(cv2.CAP_PROP_AUTO_WB)
    brightness = cap.get(cv2.CAP_PROP_BRIGHTNESS)
    contrast = cap.get(cv2.CAP_PROP_CONTRAST)
    saturation = cap.get(cv2.CAP_PROP_SATURATION)
    logger.info('Manual params: WB_TEMPERATURE readback=%.0f (requested=%s), '
                'EXPOSURE readback=%.1f, AUTO_WB readback=%s, '
                'BRIGHTNESS readback=%s, CONTRAST readback=%s, SATURATION readback=%s',
                wb_temp, args.wb_temperature, exposure,
                'n/a' if auto_wb != auto_wb else f'{auto_wb:.0f}',
                'n/a' if brightness != brightness else f'{brightness:.0f}',
                'n/a' if contrast != contrast else f'{contrast:.0f}',
                'n/a' if saturation != saturation else f'{saturation:.0f}')

    ready_path = out_dir / args.ready_file
    if ready_path.exists():
        ready_path.unlink()

    # Queue holds a few seconds of frames so brief write stalls do not drop data.
    queue_fps = args.target_fps or args.fps or 30
    frame_queue: queue.Queue[Any] = queue.Queue(maxsize=max(int(queue_fps) * 2, 30))
    stop_event = threading.Event()

    metadata_path = out_dir / 'metadata.csv'
    meta_file = open(metadata_path, 'w', newline='', encoding='utf-8-sig')
    meta_writer = csv.writer(meta_file)
    meta_writer.writerow([
        'index', 'timestamp_ns', 'pc_arrival_ns',
        'timestamp_beijing', 'filename', 'width', 'height',
    ])
    meta_lock = threading.Lock()

    # T12-P1-1: capture_loop returns bool health; run it inside a wrapper used
    # directly as the thread target so main() can detect a mid-session camera
    # death and exit non-zero.
    capture_result: dict[str, bool] = {'ok': True}

    def _run_with_result():
        capture_result['ok'] = capture_loop(
            cap, frame_queue, stop_event, ready_path, args.clock_offset_ns,
            args.warmup_s, args.target_fps)

    capture_thread = threading.Thread(
        target=_run_with_result,
        name='worker-capture',
        daemon=True,
    )
    num_writers = max(1, args.writer_threads)
    writer_threads = [
        threading.Thread(
            target=writer_loop,
            args=(frame_queue, stop_event, out_dir, source_name, args.image_format,
                  actual_w, actual_h, args.png_compression, meta_writer, meta_lock),
            name=f'worker-writer-{i}',
            daemon=True,
        )
        for i in range(num_writers)
    ]

    capture_thread.start()
    for t in writer_threads:
        t.start()

    start_ns = time.time_ns()
    end_ns = start_ns + int(args.duration * 1e9) if args.duration else None
    capture_healthy = True

    try:
        while not stop_event.is_set():
            if end_ns is not None and time.time_ns() >= end_ns:
                logger.info('Duration reached, stopping worker.')
                break
            if stop_path.exists():
                logger.info('Stop file detected, stopping worker.')
                break
            # T12-P1-1: watch the capture thread — if the camera died, exit
            # non-zero so the session notices instead of recording silently.
            # Only counts as failure when a stop was NOT requested (normal
            # stop also ends the capture loop).
            if (not stop_event.is_set() and not capture_thread.is_alive()
                    and not capture_result['ok']):
                logger.error('Capture thread reported camera failure')
                capture_healthy = False
                break
            time.sleep(0.05)
    finally:
        stop_event.set()
        capture_thread.join(timeout=2.0)
        # Allow the writers to drain the queue.
        for t in writer_threads:
            t.join(timeout=10.0)
        meta_file.close()
        cap.release()
        if stop_path.exists():
            stop_path.unlink()
        if ready_path.exists():
            ready_path.unlink()
        logger.info('Worker finished.')

    if not capture_healthy:
        sys.exit(1)


if __name__ == '__main__':
    main()
