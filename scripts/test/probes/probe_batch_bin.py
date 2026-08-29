# -*- coding: utf-8 -*-
"""Probe: batch-bin endpoint — format check + seek/preload latency."""
import json
import struct
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = 'http://127.0.0.1:8765/api/sessions/test_20260828_160624'
CAMS = ['camera_arm_2', 'camera_arm_1', 'camera_arm_3']
MAX_W = {'camera_arm_2': 640, 'camera_arm_1': 320, 'camera_arm_3': 320}


def post_bin(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def parse(data):
    count = struct.unpack('<i', data[:4])[0]
    rows = []
    off = 4
    for _ in range(count):
        row, ln = struct.unpack('<ii', data[off:off + 8])
        off += 8
        jpg = data[off:off + ln]
        off += ln
        rows.append((row, len(jpg), jpg[:2]))
    return rows


def main() -> int:
    # 1. format sanity: 3 rows, arm2
    data = post_bin('/frames/batch-bin', {'cam': 'camera_arm_2',
                                          'rows': [10, 11, 12], 'max_width': 640})
    rows = parse(data)
    print(f'[format] count={len(rows)} rows={[r[0] for r in rows]} '
          f'sizes={[r[1] for r in rows]} magic={[r[2] for r in rows]}')

    # 2. seek latency: 16-row critical window at a cold far position
    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(lambda cam: post_bin('/frames/batch-bin', {
            'cam': cam, 'rows': list(range(8000, 8016)), 'max_width': MAX_W[cam]}), CAMS))
    print(f'[seek] 16-row x3cams @8000 (cold): {(time.perf_counter()-t)*1e3:.0f} ms')

    # 3. preload steps back-to-back (16 rows x3cams), as the client runs them
    t0 = time.perf_counter()
    for step in range(8):
        rows = list(range(step * 16, step * 16 + 16))
        t = time.perf_counter()
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda cam: post_bin('/frames/batch-bin', {
                'cam': cam, 'rows': rows, 'max_width': MAX_W[cam]}), CAMS))
        print(f'  step {step}: {rows[0]}-{rows[-1]} in {(time.perf_counter()-t)*1e3:.0f} ms')
    dt = time.perf_counter() - t0
    print(f'[preload] 8 steps = 128 rows in {dt:.1f} s -> {128/dt:.0f} rows/s')
    return 0


if __name__ == '__main__':
    main()
