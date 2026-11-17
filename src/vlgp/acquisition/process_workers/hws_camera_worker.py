"""HWS capture-card worker with device-side frame timestamps.

The SDK invokes a callback for every frame and supplies ``timestamp100ns`` and
``frameIndex``.  The device timestamp is a relative, monotonic time axis, so the
first post-warm-up frame anchors it to the PC clock.  Later timestamps advance
by the device delta rather than by Python callback arrival times::

    timestamp_ns = anchor_pc_ns + (device_ns - anchor_device_ns) + clock_offset_ns

This removes callback/thread scheduling jitter from the recorded timeline while
retaining ``pc_arrival_ns`` and the raw device values for later calibration.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from vlgp.acquisition.process_workers.hws_capture import HwsCapture, HwsError
from vlgp.acquisition.process_workers.usb_camera_worker import (
    _disable_high_resolution_timer,
    _enable_high_resolution_timer,
    _set_capture_thread_priority,
    _set_process_priority_high,
    writer_loop,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class FrameCallback:
    """Decimate SDK frames on the device clock and enqueue them for writers."""

    def __init__(self, frame_queue: queue.Queue, stop_event: threading.Event,
                 ready_path: Path, warmup_s: float, target_fps: float | None,
                 clock_offset_ns: int):
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.ready_path = ready_path
        self.warmup_end = time.time() + max(0.0, warmup_s)
        self.grid_interval_ns = (
            int(round(1e9 / target_fps))
            if target_fps and target_fps > 0 else None
        )
        self.next_keep_ns: int | None = None
        self.clock_offset_ns = clock_offset_ns
        self.anchor_pc_ns: int | None = None
        self.anchor_device_ns: int | None = None
        self.last_callback_monotonic = time.monotonic()
        self.callback_count = 0
        self.kept_count = 0
        self.dropped_count = 0
        self._priority_set = False
        self._last_sdk_index: int | None = None
        self._last_device_ns: int | None = None
        self._shape: tuple[int, int] | None = None

    def __call__(self, frame, timestamp100ns: int, sdk_frame_index: int) -> None:
        if not self._priority_set:
            _set_capture_thread_priority()
            self._priority_set = True
        self.last_callback_monotonic = time.monotonic()
        self.callback_count += 1
        if self.stop_event.is_set():
            return

        pc_arrival_ns = time.time_ns()
        device_ns = int(timestamp100ns) * 100
        if time.time() < self.warmup_end:
            return

        if self._last_device_ns is not None and device_ns <= self._last_device_ns:
            logger.warning('Non-monotonic HWS device timestamp: %d after %d',
                           device_ns, self._last_device_ns)
        if (self._last_sdk_index is not None
                and sdk_frame_index != self._last_sdk_index + 1):
            logger.warning('HWS frameIndex discontinuity: %d after %d',
                           sdk_frame_index, self._last_sdk_index)
        self._last_device_ns = device_ns
        self._last_sdk_index = sdk_frame_index

        if self.grid_interval_ns is not None:
            if self.next_keep_ns is None:
                self.next_keep_ns = device_ns
            if device_ns < self.next_keep_ns:
                return
            while self.next_keep_ns <= device_ns:
                self.next_keep_ns += self.grid_interval_ns

        if self.anchor_pc_ns is None:
            self.anchor_pc_ns = pc_arrival_ns
            self.anchor_device_ns = device_ns
            logger.info('HWS clock anchor: pc_ns=%d device_timestamp100ns=%d '
                        'clock_offset_ns=%d', pc_arrival_ns, timestamp100ns,
                        self.clock_offset_ns)

        timestamp_ns = (self.anchor_pc_ns + device_ns - self.anchor_device_ns
                        + self.clock_offset_ns)
        frame_idx = self.kept_count
        shape = (frame.shape[1], frame.shape[0])
        if shape != self._shape:
            self._shape = shape
            logger.info('HWS callback frame: %dx%d, SDK frameIndex=%d',
                        shape[0], shape[1], sdk_frame_index)
        try:
            self.frame_queue.put(
                (frame_idx, timestamp_ns, pc_arrival_ns, frame,
                 int(timestamp100ns), int(sdk_frame_index)),
                timeout=0.5,
            )
        except queue.Full:
            self.dropped_count += 1
            logger.warning('Writer queue full, dropping frame %d', frame_idx)
        self.kept_count += 1

        if frame_idx == 0:
            self.ready_path.write_text(str(time.time_ns()))
            logger.info('Worker ready: HWS stream is live')


def _open_with_retry(args) -> HwsCapture:
    last_error: Exception | None = None
    attempts = max(1, int(args.open_attempts))
    for attempt in range(1, attempts + 1):
        capture = None
        logger.info('Opening HWS device %d (attempt %d/%d)',
                    args.index, attempt, attempts)
        try:
            capture = HwsCapture(args.sdk_dll)
            if attempt == 1:
                logger.info('HWS SDK version=%s devices=%s',
                            capture.version(), capture.devices())
            # The callback object is installed by main before this call.
            capture.start(args.index, args.width, args.height, args.fps,
                          args.frame_callback, args.buffer_count)
            return capture
        except (HwsError, OSError, AttributeError) as exc:
            last_error = exc
            if capture is not None:
                capture.close()
            logger.warning('HWS device %d did not open (attempt %d/%d): %s',
                           args.index, attempt, attempts, exc)
            if attempt < attempts and not args.stop_event.is_set():
                args.stop_event.wait(args.open_retry_s)
    raise HwsError(f'Cannot open HWS device {args.index} after {attempts} '
                   f'attempts: {last_error}')


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    """Keep the USB worker CLI contract so one parent can spawn either worker."""
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--fourcc', type=str, default='MJPG')
    parser.add_argument('--backend', type=str, default='dshow')
    parser.add_argument('--topic', type=str, required=True)
    parser.add_argument('--source-name', type=str, default=None)
    parser.add_argument('--out-dir', type=str, required=True)
    parser.add_argument('--duration', type=float, default=None)
    parser.add_argument('--image-format', type=str, default='png')
    parser.add_argument('--png-compression', type=int, default=1)
    parser.add_argument('--writer-threads', type=int, default=1)
    parser.add_argument('--warmup-s', type=float, default=1.5)
    parser.add_argument('--open-attempts', type=int, default=20)
    parser.add_argument('--open-retry-s', type=float, default=1.0)
    parser.add_argument('--target-fps', type=float, default=None)
    parser.add_argument('--clock-offset-ns', type=int, default=0)
    parser.add_argument('--stop-file', type=str, default='.stop')
    parser.add_argument('--ready-file', type=str, default='.ready')
    # Accepted for CLI compatibility. The endoscope/capture-card ISP owns these
    # controls; HWS SDK video input does not expose the DirectShow properties.
    parser.add_argument('--exposure', type=float, default=None)
    parser.add_argument('--wb-temperature', type=float, default=None)
    parser.add_argument('--auto-wb', type=int, default=None)
    parser.add_argument('--brightness', type=float, default=None)
    parser.add_argument('--contrast', type=float, default=None)
    parser.add_argument('--saturation', type=float, default=None)


def main() -> None:
    _enable_high_resolution_timer()
    _set_process_priority_high()
    parser = argparse.ArgumentParser(
        description='HWS capture-card worker with hardware frame timestamps')
    _add_shared_arguments(parser)
    parser.add_argument('--sdk-dll', type=str, default=None,
                        help='Path to hwscapture.dll (default: HWS_SDK_DLL or '
                             'the vendor SDK installation)')
    parser.add_argument('--buffer-count', type=int, default=4,
                        help='SDK input buffer count')
    parser.add_argument('--watchdog-s', type=float, default=5.0,
                        help='Fail if no SDK frame callback arrives for this long')
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_path = out_dir / args.stop_file
    ready_path = out_dir / args.ready_file
    for path in (stop_path, ready_path):
        if path.exists():
            path.unlink()
    source_name = (args.source_name or args.topic).replace('/', '_')
    stop_event = threading.Event()
    args.stop_event = stop_event

    queue_fps = max(args.fps or 60, int(args.target_fps or 0), 30)
    frame_queue: queue.Queue[Any] = queue.Queue(maxsize=queue_fps * 2)
    callback = FrameCallback(frame_queue, stop_event, ready_path,
                             args.warmup_s, args.target_fps,
                             args.clock_offset_ns)
    args.frame_callback = callback
    ignored = {key: getattr(args, key) for key in (
        'exposure', 'wb_temperature', 'auto_wb', 'brightness', 'contrast',
        'saturation') if getattr(args, key) is not None}
    logger.info('HWS worker starting: index=%s %sx%s@%s target_fps=%s out=%s '
                'pid=%s; DirectShow backend/fourcc are not used; '
                'ignored ISP controls=%s', args.index, args.width, args.height,
                args.fps, args.target_fps, out_dir, os.getpid(), ignored or 'none')

    capture: HwsCapture | None = None
    meta_file = None
    writer_threads: list[threading.Thread] = []
    healthy = True
    try:
        capture = _open_with_retry(args)
        meta_file = open(out_dir / 'metadata.csv', 'w', newline='',
                         encoding='utf-8-sig')
        meta_writer = csv.writer(meta_file)
        meta_writer.writerow([
            'index', 'timestamp_ns', 'pc_arrival_ns', 'timestamp_beijing',
            'filename', 'width', 'height', 'device_timestamp_100ns',
            'device_frame_index',
        ])
        meta_lock = threading.Lock()
        writer_threads = [
            threading.Thread(
                target=writer_loop,
                args=(frame_queue, stop_event, out_dir, source_name,
                      args.image_format, args.width, args.height,
                      args.png_compression, meta_writer, meta_lock),
                name=f'hws-writer-{i}', daemon=True,
            )
            for i in range(max(1, args.writer_threads))
        ]
        for thread in writer_threads:
            thread.start()

        start_ns = time.time_ns()
        end_ns = start_ns + int(args.duration * 1e9) if args.duration else None
        while not stop_event.is_set():
            if end_ns is not None and time.time_ns() >= end_ns:
                logger.info('Duration reached, stopping HWS worker.')
                break
            if stop_path.exists():
                logger.info('Stop file detected, stopping HWS worker.')
                break
            silence = time.monotonic() - callback.last_callback_monotonic
            if args.watchdog_s > 0 and silence > args.watchdog_s:
                logger.error('No HWS frame callback for %.2f s; failing worker',
                             silence)
                healthy = False
                break
            time.sleep(0.05)
    except Exception as exc:
        logger.error('HWS worker failed: %s', exc, exc_info=True)
        healthy = False
    finally:
        stop_event.set()
        if capture is not None:
            try:
                capture.close()
            except Exception:
                logger.exception('Error while closing HWS capture')
                healthy = False
        for thread in writer_threads:
            thread.join(timeout=10.0)
        if any(thread.is_alive() for thread in writer_threads):
            logger.error('HWS writer did not drain within 10 s')
            healthy = False
        if meta_file is not None:
            meta_file.close()
        for path in (stop_path, ready_path):
            if path.exists():
                path.unlink()
        logger.info('HWS worker finished: callbacks=%d kept=%d queue_dropped=%d',
                    callback.callback_count, callback.kept_count,
                    callback.dropped_count)
        _disable_high_resolution_timer()

    if not healthy:
        sys.exit(1)


if __name__ == '__main__':
    main()
