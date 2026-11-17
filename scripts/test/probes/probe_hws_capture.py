# -*- coding: utf-8 -*-
"""Probe the HWS Capture SDK end-to-end (no recording session needed).

Checks, on the real capture card:
  1. SDK version + video device enumeration (index <-> name);
  2. per-device advertised formats;
  3. a short live callback capture (default 5 s @ 1280x720/30):
     measured fps, frameIndex continuity, timestamp100ns semantics
     (stream-time vs wall-clock) and device-vs-PC clock drift.

Usage:
    python scripts/test/probes/probe_hws_capture.py [--index 0] [--seconds 5]
        [--width 1280] [--height 720] [--fps 30] [--sdk-dll PATH]
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'src'))

from vlgp.acquisition.process_workers.hws_capture import HwsCapture, HwsError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', type=int, default=0)
    ap.add_argument('--seconds', type=float, default=5.0)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--sdk-dll', default=None)
    args = ap.parse_args()

    hws = HwsCapture(args.sdk_dll)
    print('SDK version:', hws.version())
    devices = hws.devices()
    print(f'video devices: {len(devices)}')
    for d in devices:
        print(f"  [{d['index']}] {d['name']}")
        print(f"       id={d['id']}")
        fmts = hws.formats(d['index'])
        for f in fmts[:12]:
            print(f"       {f['width']}x{f['height']} @ {f['fps']:.2f} fps "
                  f"{f['pixel_format']}")
        if len(fmts) > 12:
            print(f'       ... ({len(fmts)} formats total)')

    frames: list[tuple[int, int, int]] = []  # (ts100ns, frame_index, pc_ns)
    first_bgr: list[np.ndarray] = []

    def on_frame(bgr: np.ndarray, ts100ns: int, frame_index: int) -> None:
        frames.append((ts100ns, frame_index, time.time_ns()))
        if not first_bgr:
            first_bgr.append(bgr)

    print(f'\n--- capture: device {args.index}, {args.width}x{args.height}'
          f' @ {args.fps} fps for {args.seconds:.1f} s ---')
    hws.start(args.index, args.width, args.height, args.fps, on_frame)
    t0 = time.time()
    time.sleep(args.seconds)
    hws.stop()
    hws.close()
    wall = time.time() - t0

    n = len(frames)
    print(f'frames received: {n} in {wall:.2f}s -> {n / wall:.2f} fps')
    if n < 3:
        print('FAIL: not enough frames to evaluate timestamps')
        return 1

    ts = [f[0] for f in frames]
    idx = [f[1] for f in frames]
    pc = [f[2] for f in frames]
    gaps = [idx[i + 1] - idx[i] for i in range(n - 1)]
    print(f'frameIndex: first={idx[0]} last={idx[-1]} '
          f'non-unit steps={sum(1 for g in gaps if g != 1)}')
    dts_ms = [(ts[i + 1] - ts[i]) / 1e4 for i in range(n - 1)]
    pc_ms = [(pc[i + 1] - pc[i]) / 1e6 for i in range(n - 1)]
    print(f'timestamp100ns: first={ts[0]} last={ts[-1]}')
    print(f'  device dt: mean={statistics.mean(dts_ms):.3f} ms '
          f'std={statistics.stdev(dts_ms):.3f} ms '
          f'min={min(dts_ms):.3f} max={max(dts_ms):.3f}')
    print(f'  PC     dt: mean={statistics.mean(pc_ms):.3f} ms '
          f'std={statistics.stdev(pc_ms):.3f} ms '
          f'min={min(pc_ms):.3f} max={max(pc_ms):.3f}')
    dev_span_s = (ts[-1] - ts[0]) / 1e7
    pc_span_s = (pc[-1] - pc[0]) / 1e9
    print(f'  span: device={dev_span_s:.4f}s pc={pc_span_s:.4f}s '
          f'drift={(dev_span_s - pc_span_s) * 1e3:+.2f} ms over capture')
    print(f'  first-frame device ts vs PC arrival: '
          f'{ts[0] / 1e7:.6f} (device units s) vs pc={pc[0]}')
    if pc_span_s > 0:
        print(f'  device/PC rate ratio: {dev_span_s / pc_span_s:.6f} '
              f'({(dev_span_s / pc_span_s - 1) * 1e6:+.0f} ppm)')

    if first_bgr:
        out_dir = REPO / 'var' / 'camera_test_frames'
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / 'hws_sdk_first_frame.jpg'
        cv2.imwrite(str(out), first_bgr[0])
        print(f'first frame saved: {out} shape={first_bgr[0].shape}')
    print('PROBE DONE')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except HwsError as e:
        print(f'HWS ERROR: {e}')
        raise SystemExit(2)
