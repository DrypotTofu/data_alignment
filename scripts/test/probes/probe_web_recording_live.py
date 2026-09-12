# -*- coding: utf-8 -*-
"""Live-record through the Web API and log the progress counters every 2 s.

Purpose: reproduce/falsify "已写 0 GB while recording" with the real camera path,
and produce a real (short) session for the downstream pipeline test.

The camera worker needs camera access — start the web server WITHOUT the confined
sandbox (the user's start_web_platform.bat does that naturally).

Usage:
    python scripts/test/probes/probe_web_recording_live.py --base http://127.0.0.1:8766
        [--config scripts/config/arm2_only.yaml] [--seconds 25] [--name ...]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def req(base: str, method: str, path: str, body=None, timeout: float = 120,
        raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        payload, status = e.read(), e.code
    if raw:
        return status, payload
    try:
        return status, json.loads(payload)
    except ValueError:
        return status, payload.decode('utf-8', 'replace')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8766')
    ap.add_argument('--config', default='scripts/config/arm2_only.yaml')
    ap.add_argument('--seconds', type=float, default=25.0)
    ap.add_argument('--name', default=None)
    ap.add_argument('--keep', action='store_true', help='keep the recorded session')
    ap.add_argument('--skip-preflight', action='store_true',
                    help='start the recording without running 自检 first')
    args = ap.parse_args()
    base = args.base.rstrip('/')
    cfg = str((REPO / args.config) if not Path(args.config).is_absolute() else Path(args.config))

    st, cfgs = req(base, 'GET', '/api/acquisition/configs')
    if st != 200:
        raise SystemExit(f'server not reachable / no acquisition API: {st} {cfgs}')
    name = args.name or cfgs['suggested_name']
    print(f'session name: {name}')

    if args.skip_preflight:
        pf = {'ok': None, 'failed': [], 'checks': []}
        st = 200
    else:
        st, pf = req(base, 'POST', '/api/acquisition/preflight',
                     {'config': cfg, 'reserve_minutes': 5, 'session_name': name})
    if args.skip_preflight:
        print('preflight: SKIPPED (--skip-preflight)')
    else:
        print(f"preflight: ok={pf.get('ok')} failed={pf.get('failed')}")
        for c in pf.get('checks', []):
            print(f"   {c['status']:<4} {c['label']}: {c['detail'][:110]}")

    st, started = req(base, 'POST', '/api/acquisition/start',
                      {'config': cfg, 'name': name})
    print(f'start: HTTP {st} state={started.get("state")} pid={started.get("pid")}')
    if st != 200 or started.get('busy'):
        print(json.dumps(started, ensure_ascii=False)[:300])
        return 1

    sess_dir = Path(started['session_dir']) if started.get('session_dir') else None
    t0 = time.time()
    prev = None
    try:
        while time.time() - t0 < args.seconds:
            time.sleep(2.0)
            _, s = req(base, 'GET', '/api/acquisition/status')
            pngs = len(list(sess_dir.glob('camera_arm_2/*.png'))) if sess_dir else 0
            csv_mb = 0.0
            if sess_dir:
                csv_mb = sum(p.stat().st_size for p in
                             sess_dir.glob('robot_pose/**/*.csv')) / 1048576
            line = (f"t={s['elapsed_s']:>5}s state={s['state']:<9} "
                    f"gb={s['gb_written']} mb_per_s={s['mb_per_s']} "
                    f"| pngs={pngs} csv={csv_mb:.1f}MB")
            print(line, flush=True)
            if prev is not None and s['gb_written'] == 0 and prev == 0 and pngs == 0:
                print('  >>> counter stuck at 0 (reproduced)')
            prev = s['gb_written']
    finally:
        print('stopping...')
        st, stopped = req(base, 'POST', '/api/acquisition/stop')
        final = stopped.get('status', {})
        print(f"stop: HTTP {st} state={final.get('state')} "
              f"exit={final.get('exit_code')} gb={final.get('gb_written')} "
              f"waited={stopped.get('waited_s')}s")
        st, log = req(base, 'GET', '/api/acquisition/log', raw=True)
        if isinstance(log, bytes):
            log = log.decode('utf-8', 'replace')
        lines = [ln for ln in str(log).splitlines() if ln.startswith('=== ACQ')]
        print('markers:')
        for ln in lines:
            print('  ', ln)
        if sess_dir and sess_dir.is_dir():
            pngs = len(list(sess_dir.glob('camera_arm_2/*.png')))
            total = sum(p.stat().st_size for p in sess_dir.rglob('*') if p.is_file())
            meta = sess_dir / 'camera_arm_2' / 'metadata.csv'
            rows = (sum(1 for _ in meta.open(encoding='utf-8-sig')) - 1) if meta.exists() else 0
            print(f'session: {sess_dir}')
            print(f'  camera_arm_2: {pngs} png, metadata rows={rows}')
            print(f'  total size: {total / 1048576:.1f} MB')
            robots = sorted(sess_dir.glob('robot_pose/robot_pose_arm*/twincat_poses.csv'))
            for r in robots:
                n = sum(1 for _ in r.open(encoding='utf-8-sig')) - 1
                print(f'  {r.parent.name}: {n} rows, {r.stat().st_size / 1048576:.1f} MB')
            print(f'  session.json: {(sess_dir / "session.json").exists()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
