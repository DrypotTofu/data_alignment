# -*- coding: utf-8 -*-
"""Manual check of POST /api/sessions/{name}/delete?mode=permanent (debug helper).

Usage: python scripts/test/probes/probe_delete_manual.py [--name del_manual]
"""
from __future__ import annotations

import argparse
import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO / 'var' / 'test_fixtures' / 'synth_3cam'
FIXTURE_SESSION = 'synth_3cam_0001'


def req(base: str, method: str, path: str, body=None, timeout: float = 600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode('utf-8', 'replace')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8766')
    ap.add_argument('--name', default='del_manual')
    args = ap.parse_args()
    base = args.base.rstrip('/')
    name = args.name
    dst = FIXTURE_ROOT / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(FIXTURE_ROOT / FIXTURE_SESSION, dst)

    print('trash before:', json.dumps(req(base, 'GET', '/api/trash')[1],
                                      ensure_ascii=False)[:200])
    st, out = req(base, 'POST', f'/api/sessions/{name}/delete?mode=permanent&confirm=1')
    print(f'permanent delete -> HTTP {st}')
    print(' response:', json.dumps(out, ensure_ascii=False)[:400])
    print(' dir exists now:', dst.exists())
    st, out2 = req(base, 'POST', f'/api/trash/empty', {'confirm': True})
    print(f'empty trash -> HTTP {st}:', json.dumps(out2, ensure_ascii=False)[:200])
    if dst.exists():
        shutil.rmtree(dst)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
