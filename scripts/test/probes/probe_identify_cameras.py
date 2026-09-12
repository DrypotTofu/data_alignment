# -*- coding: utf-8 -*-
"""Identify which camera index is which: grab a frame + probe resolutions.

Saves `var/camera_id/index<N>_<w>x<h>.png` so a human (or the agent) can look at
the picture and say "this is the endoscope" / "this is the laptop webcam".
Needs camera access (in the DSH harness: danger-full-access, since the confined
sandbox hides UVC devices).

Usage:  python scripts/test/probes/probe_identify_cameras.py [--indexes 0,1] [--open-w 1280 --open-h 720]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / 'var' / 'camera_id'


def grab(index: int, w: int, h: int, settle_s: float = 1.0):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        return None, {}
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        time.sleep(settle_s)
        meta = {'reported_w': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                'reported_h': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                'reported_fps': round(float(cap.get(cv2.CAP_PROP_FPS)), 2),
                'fourcc': int(cap.get(cv2.CAP_PROP_FOURCC))}
        frame = None
        for _ in range(20):
            ok, f = cap.read()
            if ok and f is not None and f.size > 0:
                frame = f
                break
            time.sleep(0.05)
        return frame, meta
    finally:
        cap.release()


def probe_modes(index: int) -> list[dict]:
    out = []
    for (w, h) in ((1920, 1080), (1280, 720), (1024, 768), (800, 600), (640, 480)):
        frame, meta = grab(index, w, h, settle_s=0.6)
        out.append({'requested': f'{w}x{h}',
                    'actual': f"{meta.get('reported_w')}x{meta.get('reported_h')}",
                    'fps': meta.get('reported_fps'),
                    'frame_read': frame is not None,
                    'mean': (round(float(frame.mean()), 1) if frame is not None else None)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--indexes', default='0,1')
    ap.add_argument('--open-w', type=int, default=1280)
    ap.add_argument('--open-h', type=int, default=720)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    indexes = [int(x) for x in args.indexes.split(',') if x.strip() != '']
    report = {}
    for idx in indexes:
        print(f'--- index {idx} ---', flush=True)
        frame, meta = grab(idx, args.open_w, args.open_h)
        if frame is None:
            print('  打不开 / 取不到帧')
            report[str(idx)] = {'opened': False}
            continue
        h, w = frame.shape[:2]
        path = OUT / f'index{idx}_{w}x{h}.png'
        cv2.imwrite(str(path), frame)
        modes = probe_modes(idx)
        report[str(idx)] = {'opened': True, 'saved': str(path), 'size': f'{w}x{h}',
                            'meta': meta, 'modes': modes,
                            'mean': round(float(frame.mean()), 1)}
        print(f'  抓到帧 {w}x{h} -> {path}（平均亮度 {frame.mean():.1f}）')
        for m in modes:
            print(f"    {m['requested']:>9} -> {m['actual']:>9} @{m['fps']} "
                  f"frame={m['frame_read']} mean={m['mean']}")
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding='utf-8')
    print(f'\nreport: {OUT / "report.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
