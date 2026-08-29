# -*- coding: utf-8 -*-
"""Minimal preload diagnosis: start, poll status every 1s, print progress."""
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:8766'
SESS = 'fps_check'


def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())


def post(path, timeout=10):
    req = urllib.request.Request(BASE + path, data=b'', method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    print('[start] POST /preload ...', flush=True)
    print(post(f'/api/sessions/{SESS}/preload'), flush=True)
    for i in range(30):
        t = time.perf_counter()
        try:
            st = get(f'/api/sessions/{SESS}/preload/status')
            print(f'  t={i + 1:2d}s status={st}', flush=True)
        except Exception as e:  # noqa: BLE001
            print(f'  t={i + 1:2d}s STATUS FAILED: {e}', flush=True)
            time.sleep(1)
            continue
        if st['status'] != 'running':
            break
        time.sleep(1.0)
    return 0


if __name__ == '__main__':
    main()
