# -*- coding: utf-8 -*-
"""Probe: simulate the web client's fetch pattern against the running server
(preload steps + critical-path fetches + row-status) and measure the real
frame-fill rate and seek latency. Mirrors app.js behaviour with
FETCH_CHUNK=16, 3-parallel preload, critical 5-row fetches, row-status
refresh every 400 ms."""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = 'http://127.0.0.1:8765/api/sessions/test_20260828_160624'
CAMS = ['camera_arm_2', 'camera_arm_1', 'camera_arm_3']
MAX_W = {'camera_arm_2': 640, 'camera_arm_1': 320, 'camera_arm_3': 320}
ROWS = 13343


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def batch(cam, rows):
    return post('/frames/batch', {'cam': cam, 'rows': rows, 'max_width': MAX_W[cam]})


def row_status(row):
    with urllib.request.urlopen(f'{BASE}/row/{row}', timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    # ---- 1. seek latency: critical 5-row fetch (cold, far from cache)
    for row in (6000,):
        t = time.perf_counter()
        batch('camera_arm_2', list(range(row, row + 5)))
        batch('camera_arm_1', list(range(row, row + 5)))
        batch('camera_arm_3', list(range(row, row + 5)))
        print(f'[seek] critical 5-row x3cams @{row}: {(time.perf_counter()-t)*1e3:.0f} ms')

    # ---- 2. sustained preload pattern: 3-parallel 16-row chunks, back-to-back
    # (the client runs one step every ~300ms; here we time 12 steps)
    filled = set()
    t0 = time.perf_counter()
    for step in range(12):
        rows = list(range(step * 16, step * 16 + 16))
        t = time.perf_counter()
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda cam: batch(cam, rows), CAMS))
        filled.update(rows)
        print(f'  step {step}: rows {rows[0]}-{rows[-1]} in {(time.perf_counter()-t)*1e3:.0f} ms')
    dt = time.perf_counter() - t0
    print(f'[preload] 12 steps = {192} rows in {dt:.1f} s -> {192/dt:.0f} rows/s')

    # ---- 3. row-status cost under load
    t = time.perf_counter()
    for _ in range(5):
        row_status(2000)
    print(f'[row-status] mean {(time.perf_counter()-t)/5*1e3:.1f} ms')

    # ---- 4. critical-path single fetch during playback (current-row + 4)
    t = time.perf_counter()
    batch('camera_arm_2', list(range(3000, 3005)))
    print(f'[critical] 5-row arm2: {(time.perf_counter()-t)*1e3:.0f} ms')
    return 0


if __name__ == '__main__':
    main()
