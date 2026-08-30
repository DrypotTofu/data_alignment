# -*- coding: utf-8 -*-
"""Probe: re-verify IMREAD_REDUCED_COLOR_2 decode speedup on the BIG session
(cold disk, interleaved rows), plus measure per-row filename-lookup and
Path.exists() costs (preload micro-optimizations)."""
import time
from pathlib import Path

import cv2
import pandas as pd

from vlgp.web.session_index import SessionIndex

index = SessionIndex()
session = 'test_20260828_160624'
cam = 'camera_arm_2'
df = index.alignment(session)
col = 'anchor_filename'
rows = list(range(3000, 3200, 3))          # interleaved, cold-ish

paths = []
for r in rows:
    fname = df.iloc[r][col]
    paths.append(index.session_dir(session) / cam / fname)


def bench(fn, n=6):
    t = time.perf_counter()
    for _ in range(n):
        for p in paths:
            fn(p)
    return (time.perf_counter() - t) / (n * len(paths)) * 1e3


full = bench(lambda p: cv2.imread(str(p)))
red2 = bench(lambda p: cv2.imread(str(p), cv2.IMREAD_REDUCED_COLOR_2))
print(f'big-session arm2: full {full:5.1f} ms/row | REDUCED_2 {red2:5.1f} ms/row '
      f'({full / max(red2, 1e-9):.2f}x)')

# filename lookup cost (pandas iloc) per row
t = time.perf_counter()
n = 2000
for i in range(n):
    df.iloc[i][col]
print(f'df.iloc filename lookup: {(time.perf_counter()-t)/n*1e6:.0f} us/row')

# Path.exists() cost
t = time.perf_counter()
for p in paths[:500]:
    p.exists()
print(f'Path.exists(): {(time.perf_counter()-t)/500*1e6:.0f} us/row')

# listdir as an alternative (one scan for many rows)
d = index.session_dir(session) / cam
t = time.perf_counter()
_ = set(d.iterdir())
print(f'os iter one dir: {(time.perf_counter()-t)*1e3:.1f} ms for whole dir')
