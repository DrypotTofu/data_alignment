"""Exposure sweep probe for the wrist cameras (arm_1 / arm_3).

Purpose (2026-08-23): sweep CAP_PROP_EXPOSURE from -6 up to 0 in unit steps,
record a few seconds per level at full read speed (no target_fps throttling)
and measure the native achievable frame rate at each exposure. Exposure is
log2 seconds on these DSHOW cameras, so -5 (= 1/32 s) already approaches the
30 fps frame period; levels above that are expected to cap the rate.

Cameras are swept sequentially (one open at a time) to avoid the DSHOW
multi-open conflicts that motivated process isolation in the acquisition
framework.

Output is packaged as a session directory so it can be opened in
review_session.py (metadata fallback view):

    sessions/exposure_sweep_<YYYYMMDD_HHMMSS>/
        camera_arm_1/camera_arm_1_*.png + metadata.csv
        camera_arm_3/...
        sweep_summary.csv   # per camera x level: frames, fps, readbacks
        session.json        # run parameters

Usage:
    python scripts/test/probes/exposure_sweep.py [--seconds 3.0]
"""
from __future__ import annotations

import argparse
import csv
import json
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

# Match scripts/config/default.yaml wrist-camera settings.
CAMERAS = {'camera_arm_1': 1, 'camera_arm_3': 3}
WIDTH, HEIGHT = 640, 480
FOURCC = 'MJPG'
FPS_REQUEST = 60  # driver streaming mode request (same as acquisition)
BRIGHTNESS, CONTRAST, SATURATION = 36, 5, 64
AUTO_WB = 1
DEFAULT_LEVELS = [-6, -5, -4, -3, -2, -1, 0]
SETTLE_S = 0.4  # drop frames while the driver applies the new exposure

REPO_ROOT = Path(__file__).resolve().parents[3]


def _writer(frame_queue: queue.Queue, stop: threading.Event, out_dir: Path,
            source_name: str, meta_writer: csv.writer, meta_lock: threading.Lock) -> None:
    while True:
        try:
            idx, ts, level, frame = frame_queue.get(timeout=0.1)
        except queue.Empty:
            if stop.is_set() and frame_queue.empty():
                return
            continue
        filename = f'{source_name}_{idx:08d}_{ts}.png'
        cv2.imwrite(str(out_dir / filename), frame,
                    [cv2.IMWRITE_PNG_COMPRESSION, 1])
        with meta_lock:
            meta_writer.writerow([idx, ts, ts, filename, WIDTH, HEIGHT, level])


def sweep_camera(cam_name: str, cam_index: int, session_dir: Path,
                 levels: list[int], seconds: float) -> list[dict]:
    out_dir = session_dir / cam_name
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open {cam_name} (index {cam_index}) with dshow')

    # Mirror usb_camera_worker.py property order (order matters on these cameras).
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # manual exposure
    cap.set(cv2.CAP_PROP_EXPOSURE, levels[0])
    cap.set(cv2.CAP_PROP_AUTO_WB, float(AUTO_WB))
    cap.set(cv2.CAP_PROP_BRIGHTNESS, BRIGHTNESS)
    cap.set(cv2.CAP_PROP_CONTRAST, CONTRAST)
    cap.set(cv2.CAP_PROP_SATURATION, SATURATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS_REQUEST)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC))

    meta_file = open(out_dir / 'metadata.csv', 'w', newline='', encoding='utf-8-sig')
    meta_writer = csv.writer(meta_file)
    meta_writer.writerow(['index', 'timestamp_ns', 'pc_arrival_ns',
                          'filename', 'width', 'height', 'exposure_level'])
    meta_lock = threading.Lock()
    frame_queue: queue.Queue = queue.Queue(maxsize=300)
    stop = threading.Event()
    writer_thread = threading.Thread(
        target=_writer, args=(frame_queue, stop, out_dir, cam_name, meta_writer, meta_lock),
        daemon=True)
    writer_thread.start()

    results: list[dict] = []
    frame_idx = 0
    try:
        for level in levels:
            cap.set(cv2.CAP_PROP_EXPOSURE, level)
            readback = cap.get(cv2.CAP_PROP_EXPOSURE)
            # Settle: discard frames until the new exposure is in effect.
            t_settle = time.perf_counter() + SETTLE_S
            while time.perf_counter() < t_settle:
                cap.read()
            # Record window: read as fast as the camera delivers.
            t0 = time.perf_counter()
            n0 = frame_idx
            while time.perf_counter() - t0 < seconds:
                ok, frame = cap.read()
                if not ok:
                    continue
                try:
                    frame_queue.put((frame_idx, time.perf_counter_ns(), level, frame),
                                    timeout=0.5)
                except queue.Full:
                    pass
                frame_idx += 1
            elapsed = time.perf_counter() - t0
            frames = frame_idx - n0
            fps = frames / elapsed if elapsed > 0 else 0.0
            results.append({
                'camera': cam_name, 'exposure_level': level,
                'exposure_ms': round(1000.0 * (2.0 ** level), 2),
                'exposure_readback': round(readback, 2),
                'frames': frames, 'window_s': round(elapsed, 3),
                'measured_fps': round(fps, 2),
            })
            print(f'  {cam_name} exposure={level:+d} '
                  f'(~{1000.0 * 2.0 ** level:6.1f} ms) readback={readback:+.1f} '
                  f'frames={frames:4d} in {elapsed:.2f}s -> {fps:6.2f} fps')
    finally:
        stop.set()
        writer_thread.join(timeout=30)
        meta_file.close()
        cap.release()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--seconds', type=float, default=3.0,
                        help='record duration per exposure level (default 3.0)')
    parser.add_argument('--levels', type=int, nargs='*', default=DEFAULT_LEVELS,
                        help='exposure levels to sweep (default -6..0)')
    args = parser.parse_args()

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_dir = REPO_ROOT / 'sessions' / f'exposure_sweep_{stamp}'
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f'Session dir: {session_dir}')

    all_results: list[dict] = []
    for cam_name, cam_index in CAMERAS.items():
        print(f'=== {cam_name} (index {cam_index}) ===')
        all_results.extend(sweep_camera(cam_name, cam_index, session_dir,
                                        args.levels, args.seconds))

    with open(session_dir / 'sweep_summary.csv', 'w', newline='',
              encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
        writer.writeheader()
        writer.writerows(all_results)
    with open(session_dir / 'session.json', 'w', encoding='utf-8') as f:
        json.dump({
            'type': 'exposure_sweep_probe',
            'created': stamp,
            'cameras': CAMERAS,
            'levels': args.levels,
            'seconds_per_level': args.seconds,
            'settings': {'width': WIDTH, 'height': HEIGHT, 'fourcc': FOURCC,
                         'fps_request': FPS_REQUEST, 'brightness': BRIGHTNESS,
                         'contrast': CONTRAST, 'saturation': SATURATION,
                         'auto_wb': AUTO_WB},
        }, f, indent=2, ensure_ascii=False)
    print(f'Summary: {session_dir / "sweep_summary.csv"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
