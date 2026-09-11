# -*- coding: utf-8 -*-
"""Browsing check for an external backup root, via the running server.

Read-only with respect to the inspected folder: it switches the active root and
calls only listing endpoints (no frame requests, so no preview cache is written
into the external folder). Restores the original root at the end.

Usage:  python scripts/test/probes/probe_browse_external_root.py [--path E:\\sessions] [--base URL]
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request


def req(base: str, method: str, path: str, body=None, timeout: float = 300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode('utf-8', 'replace')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8766')
    ap.add_argument('--path', default=r'E:\sessions')
    args = ap.parse_args()
    base = args.base.rstrip('/')

    _, before = req(base, 'GET', '/api/settings')
    original = before['root']
    ok = True
    try:
        st, sw = req(base, 'PUT', '/api/settings/root', {'path': args.path})
        print(f'switch -> {args.path}: HTTP {st}')
        if st != 200:
            print('  refused:', sw)
            return 1
        print(f"  root={sw['root']}")
        print(f"  writable={sw['writable']}  sessions={sw['n_sessions']}"
              f"  disk={sw['disk']}")
        print(f"  cache_dir={sw['cache_dir']}")
        st, lst = req(base, 'GET', '/api/fs/list?path='
                      + urllib.parse.quote(args.path))
        print(f"  fs/list: {st}, {len(lst.get('entries', []))} entries, "
              f"n_sessions={lst.get('n_sessions')}")
        st, sess = req(base, 'GET', '/api/sessions')
        print(f"  /api/sessions: {st}, {len(sess['sessions'])} sessions")
        for s in sess['sessions']:
            cams = ','.join(k[-1] for k, v in s['cameras'].items() if v)
            ann = s.get('annotations') or {}
            print(f"    {s['name']:<24} rows={s['n_rows']:>6} "
                  f"align={str(s['has_alignment']):<5} cams=[{cams}] "
                  f"status={ann.get('status', 'pending')}"
                  f" trim={ann.get('trim')}")
        unaligned = [s['name'] for s in sess['sessions'] if not s['has_alignment']]
        print(f"  unaligned sessions (opening one writes an alignment table "
              f"into that folder!): {unaligned}")
        ok = st == 200 and len(sess['sessions']) == sw['n_sessions']
    finally:
        st, back = req(base, 'PUT', '/api/settings/root', {'path': original})
        print(f'restored root -> {original} (HTTP {st})')
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
