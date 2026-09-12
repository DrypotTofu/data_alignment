# -*- coding: utf-8 -*-
"""Run the web regression suite twice: repo sessions root and the 3-camera
synthetic fixture root (switched through the picker API), then restore.

Why: every session on disk is a single-camera recording, so the wrist-camera
checks are skipped there. Pointing the platform at the fixture root exercises
them — and doubles as a check that the suite follows the ACTIVE data root
instead of the hardcoded repo ``sessions/`` folder.

Usage:  python scripts/test/probes/run_regression_all_roots.py [--base URL]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO / 'var' / 'test_fixtures' / 'synth_3cam'


def api(base: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read())


def switch_root(base: str, path: str, tries: int = 120, delay: float = 5.0) -> dict:
    """Switch the data root, waiting out the 409 busy guard.

    A preload started by an earlier probe can still be encoding; the server
    refuses to switch while it runs (its paths belong to the old root).
    """
    for i in range(tries):
        try:
            return api(base, 'PUT', '/api/settings/root', {'path': path})
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            detail = json.loads(e.read()).get('detail', {})
            print(f'  busy: {detail.get("busy")} — retry in {delay:.0f}s '
                  f'({i + 1}/{tries})', flush=True)
            time.sleep(delay)
    raise SystemExit('still busy after waiting: is a preload stuck?')


def run_suite(base: str, extra: list[str] | None = None) -> int:
    """Run the regression; a root with no aligned session counts as SKIP, not a
    failure (the repo sessions/ folder is legitimately empty once the data has
    been archived to another disk)."""
    _, body = api(base, 'GET', '/api/sessions')
    aligned = [s['name'] for s in body['sessions'] if s.get('n_rows')]
    if not aligned:
        print(f'>>> SKIP: no aligned session in {body.get("root")}', flush=True)
        return 0
    cmd = [sys.executable, '-u',
           str(REPO / 'scripts' / 'test' / 'verification' / 'web_regression.py'),
           '--base', base] + (extra or [])
    print(f'\n########## {" ".join(cmd[2:])} ##########', flush=True)
    return subprocess.call(cmd, cwd=str(REPO))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8766')
    args = ap.parse_args()
    base = args.base.rstrip('/')

    if not FIXTURE_ROOT.is_dir():
        print('building fixture session...')
        subprocess.check_call([sys.executable, '-u',
                               str(REPO / 'scripts' / 'test' / 'fixtures'
                                   / 'build_fixture_session.py')], cwd=str(REPO))

    original = api(base, 'GET', '/api/settings')['root']
    rc = 0
    try:
        rc |= run_suite(base)
        switch_root(base, str(FIXTURE_ROOT))
        print(f'>>> active root switched to fixture: {FIXTURE_ROOT}')
        rc |= run_suite(base)
    finally:
        try:
            switch_root(base, original)
            print(f'>>> active root restored: {original}')
        except SystemExit as e:
            print(f'>>> WARNING: could not restore the active root: {e}')
        # the fixture run leaves annotations/manifest in the fixture root
        for p in (FIXTURE_ROOT / 'review_manifest.json',
                  FIXTURE_ROOT / 'synth_3cam_0001' / 'annotations.json'):
            if p.exists():
                p.unlink()
    print('ALL-ROOTS RESULT:', 'PASS' if rc == 0 else 'FAIL')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
