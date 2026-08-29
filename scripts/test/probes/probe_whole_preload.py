# -*- coding: utf-8 -*-
"""Probe: whole-session preload end-to-end (POST /preload -> poll status ->
disk cache -> post-preload batch latency)."""
import json
import time
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8766'
SESS = 'fps_check'


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> int:
    # 1. start preload
    t0 = time.perf_counter()
    st = req('POST', f'/api/sessions/{SESS}/preload')
    print(f'[start] {st}')

    # 2. poll until done
    last = None
    while True:
        st = req('GET', f'/api/sessions/{SESS}/preload/status')
        if st != last:
            print(f'  poll: status={st["status"]} current={st["current"]}/{st["total"]} '
                  f'seg={st["segment"] + 1}/{st["segments"]}')
            last = st
        if st['status'] != 'running':
            break
        time.sleep(1.0)
    print(f'[done] in {(time.perf_counter()-t0):.1f} s, segments={st["segments"]}')

    # 3. disk cache files
    root = Path('var/web_preview')
    n = sum(1 for _ in root.rglob('*.jpg')) if root.exists() else 0
    size = sum(p.stat().st_size for p in root.rglob('*.jpg')) if root.exists() else 0
    print(f'[disk] preview cache: {n} files, {size/1e6:.1f} MB')

    # 4. idempotent re-run after completion
    st = req('POST', f'/api/sessions/{SESS}/preload')
    print(f'[restart] {st["status"]}')

    # 5. batch latency now (should be disk reads)
    body = {'cam': 'camera_arm_2', 'rows': list(range(500, 524)), 'max_width': 640}
    req_u = urllib.request.Request(BASE + f'/api/sessions/{SESS}/frames/batch-bin',
                                   data=json.dumps(body).encode(),
                                   headers={'Content-Type': 'application/json'},
                                   method='POST')
    t = time.perf_counter()
    with urllib.request.urlopen(req_u, timeout=30) as r:
        data = r.read()
    n = int.from_bytes(data[:4], 'little', signed=True)
    print(f'[batch-bin 24 rows after preload] {(time.perf_counter()-t)*1e3:.0f} ms, '
          f'{n} images, total {len(data)/1e6:.2f} MB')
    return 0


if __name__ == '__main__':
    main()
