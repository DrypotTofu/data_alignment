# -*- coding: utf-8 -*-
"""Verify a recorded session's content quality (frames, rate, robot data).

Reads the session on disk and checks the numbers that matter after a recording:
camera frame count and effective FPS from metadata.csv timestamps, per-arm robot
row counts, session.json duration/exit codes, and the worker log presence.

Usage:  python scripts/test/probes/probe_verify_session.py sessions/<name>
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('session', nargs='?', default='sessions/arm2_real_1')
    ap.add_argument('--expect-fps', type=float, default=30.0)
    ap.add_argument('--tolerance', type=float, default=0.06,
                    help='allowed relative FPS deviation (0.06 = 6%)')
    args = ap.parse_args()
    sess = Path(args.session)
    if not sess.is_absolute():
        sess = REPO / sess
    print(f'=== session: {sess} ===')
    ok = True

    for cam_dir in sorted(p for p in sess.glob('camera_arm_*') if p.is_dir()):
        pngs = list(cam_dir.glob('*.png')) + list(cam_dir.glob('*.jpg'))
        meta = cam_dir / 'metadata.csv'
        rows = list(csv.DictReader(meta.open(encoding='utf-8-sig'))) if meta.exists() else []
        fps = None
        span = None
        if len(rows) > 1:
            span = (int(rows[-1]['timestamp_ns']) - int(rows[0]['timestamp_ns'])) / 1e9
            fps = (len(rows) - 1) / span if span > 0 else 0.0
        w = rows[0].get('width') if rows else '?'
        h = rows[0].get('height') if rows else '?'
        print(f'{cam_dir.name}: {len(pngs)} frames, metadata rows={len(rows)}, '
              f'{w}x{h}, span={span:.2f}s, fps={fps:.2f}' if fps is not None else
              f'{cam_dir.name}: {len(pngs)} frames, metadata rows={len(rows)}')
        dev = abs(fps - args.expect_fps) / args.expect_fps if fps else 1.0
        good = pngs and len(rows) == len(pngs) and dev <= args.tolerance
        ok &= bool(good)
        print(f"   [{'PASS' if good else 'FAIL'}] frames match metadata + "
              f"fps within {args.tolerance * 100:.0f}%")
        wlog = cam_dir / 'worker.log'
        print(f"   worker.log: {'yes, %d lines' % sum(1 for _ in wlog.open(encoding='utf-8')) if wlog.exists() else 'MISSING'}")

    arm_files = sorted(sess.glob('robot_pose/robot_pose_arm*/twincat_poses.csv'))
    if not arm_files:
        print('robot_pose: no per-arm CSVs')
        ok = False
    for r in arm_files:
        n = sum(1 for _ in r.open(encoding='utf-8-sig')) - 1
        print(f'  {r.parent.name}: {n} rows ({r.stat().st_size / 1048576:.1f} MB)')
        if n <= 0:
            ok = False

    sj_path = sess / 'session.json'
    if sj_path.exists():
        sj = json.loads(sj_path.read_text(encoding='utf-8'))
        print(f"session.json: duration={sj['duration_s']:.1f}s "
              f"samples={sj['samples_written']} "
              f"worker_exit={sj.get('worker_exit_codes')}")
        bad = {k: v for k, v in (sj.get('worker_exit_codes') or {}).items() if v not in (0, None)}
        if bad:
            print(f'   [FAIL] worker exit codes: {bad}')
            ok = False
    else:
        print('session.json MISSING')
        ok = False

    total = sum(p.stat().st_size for p in sess.rglob('*') if p.is_file())
    print(f'total size: {total / 1048576:.0f} MB')
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
