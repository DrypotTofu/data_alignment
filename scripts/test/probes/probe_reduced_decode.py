# -*- coding: utf-8 -*-
"""Probe: cv2.IMREAD_REDUCED_COLOR_2 (half-resolution PNG decode) speedup
for the web frame preview path, and its visual equivalence vs full decode +
INTER_AREA resize."""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

SESS = Path(r'sessions/fps_check')


def find_png(cam: str) -> Path:
    d = SESS / cam
    pngs = sorted(d.glob('*.png'))
    if not pngs:
        raise FileNotFoundError(f'no pngs under {d}')
    return pngs[len(pngs) // 2]


def main() -> int:
    for cam, max_width in (('camera_arm_2', 640), ('camera_arm_1', 320)):
        p = find_png(cam)
        n = 5

        t0 = time.perf_counter()
        full = None
        for _ in range(n):
            full = cv2.imread(str(p))
        t_full_read = (time.perf_counter() - t0) / n

        h, w = full.shape[:2]
        scale = max_width / w
        t0 = time.perf_counter()
        resized = None
        for _ in range(n):
            resized = cv2.resize(full, (max_width, max(1, int(h * scale))),
                                 interpolation=cv2.INTER_AREA)
        t_resize = (time.perf_counter() - t0) / n

        t0 = time.perf_counter()
        reduced = None
        for _ in range(n):
            reduced = cv2.imread(str(p), cv2.IMREAD_REDUCED_COLOR_2)
        t_reduced = (time.perf_counter() - t0) / n
        rh, rw = reduced.shape[:2]
        # half-res decode may yield width > max_width; downscale if needed
        if rw > max_width:
            reduced = cv2.resize(reduced, (max_width, max(1, int(rh * max_width / rw))),
                                 interpolation=cv2.INTER_AREA)

        mean_diff = float(np.abs(resized.astype(np.float32)
                                 - reduced.astype(np.float32)).mean())
        print(f'{cam}: src {w}x{h} -> target {max_width}')
        print(f'  full read {t_full_read * 1e3:6.1f} ms | resize {t_resize * 1e3:5.1f} ms | '
              f'reduced read {t_reduced * 1e3:6.1f} ms  ({t_full_read / max(t_reduced, 1e-9):.1f}x)')
        print(f'  reduced shape {reduced.shape[:2]} vs resized {resized.shape[:2]}, '
              f'mean|diff| = {mean_diff:.3f} (0..255)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
