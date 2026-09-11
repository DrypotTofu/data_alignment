# -*- coding: utf-8 -*-
"""Run the real stale-cache cleanup through the API (user request 2026-09-11).

Scans /api/settings/cache, prints what is stale, then POSTs
/api/settings/cache/clean with confirm=true and reports the freed space.
Deleting ~370k small files takes minutes; the request timeout is generous.

Usage:  python scripts/test/probes/probe_cache_clean.py [--base URL] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def req(base: str, method: str, path: str, body=None, timeout: float = 3600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8766')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    base = args.base.rstrip('/')

    print('--- scan ---')
    t0 = time.perf_counter()
    scan = req(base, 'GET', '/api/settings/cache')
    print(f"  scanned in {time.perf_counter() - t0:.2f} s")
    print(f"  cache roots: {scan['cache_roots']}")
    print(f"  total {scan['total_mb']} MB / {len(scan['dirs'])} cache dirs; "
          f"stale {scan['stale_mb']} MB / {scan['stale_count']}")
    for d in scan['dirs']:
        if d['stale']:
            print(f"    STALE {d['session']:<26} {d['mb']:>9.1f} MB  "
                  f"{d['files']:>7} files")
    for d in scan['dirs']:
        if not d['stale']:
            print(f"    keep  {d['session']:<26} {d['mb']:>9.1f} MB  "
                  f"{d['files']:>7} files")
    if args.dry_run:
        print('dry run: nothing deleted')
        return 0
    if not scan['stale_count']:
        print('nothing stale')
        return 0

    print('--- clean (confirm=true) ---')
    t0 = time.perf_counter()
    out = req(base, 'POST', '/api/settings/cache/clean', {'confirm': True})
    dt = time.perf_counter() - t0
    print(f"  deleted {out['removed_count']} dirs, freed {out['freed_mb']} MB "
          f"in {dt:.1f} s")
    for r in out['removed']:
        print(f"    removed {r['session']} ({r['mb']} MB)")
    for f in out['failed']:
        print(f"    FAILED {f}")

    print('--- verify ---')
    scan2 = req(base, 'GET', '/api/settings/cache')
    legacy = REPO / 'var' / 'web_preview'
    print(f"  after: {scan2['total_mb']} MB / {len(scan2['dirs'])} dirs, "
          f"stale {scan2['stale_count']}")
    print(f"  legacy dir exists: {legacy.exists()}"
          + (f" ({len(list(legacy.iterdir()))} entries)" if legacy.exists() else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
