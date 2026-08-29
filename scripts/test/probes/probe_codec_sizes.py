# -*- coding: utf-8 -*-
"""Probe: JPEG vs WebP size/encode-time on a real session frame."""
import time
from pathlib import Path

import cv2

p = sorted(Path('sessions/fps_check/camera_arm_2').glob('*.png'))[20]
img = cv2.imread(str(p))
img = cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA)

tests = [
    ('jpg82', '.jpg', [cv2.IMWRITE_JPEG_QUALITY, 82]),
    ('jpg75', '.jpg', [cv2.IMWRITE_JPEG_QUALITY, 75]),
    ('jpg70', '.jpg', [cv2.IMWRITE_JPEG_QUALITY, 70]),
    ('webp80', '.webp', [cv2.IMWRITE_WEBP_QUALITY, 80]),
    ('webp70', '.webp', [cv2.IMWRITE_WEBP_QUALITY, 70]),
]
for name, ext, params in tests:
    t = time.perf_counter()
    ok, buf = cv2.imencode(ext, img, params)
    dt = (time.perf_counter() - t) * 1e3
    print(f'{name}: {len(buf):6d} B  {dt:5.1f} ms')
