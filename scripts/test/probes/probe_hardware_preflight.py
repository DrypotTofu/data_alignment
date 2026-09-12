# -*- coding: utf-8 -*-
"""Real-hardware preflight (no server): which camera indices open + PLC/ADS read.

Read-only-ish: it opens each camera index for one frame and reads a few PLC
symbols over ADS — the same checks the Web「采集」panel runs before recording.
Nothing is written except the probe's own console output.

Usage:
    python scripts/test/probes/probe_hardware_preflight.py [--config scripts/config/default.yaml]
                                                           [--max-index 3] [--no-cameras] [--no-robot]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'src'))

from vlgp.web.camera_service import (  # noqa: E402
    _robot_check,
    config_cameras,
    preflight,
    scan_cameras,
    suggest_session_name,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=str(REPO / 'scripts' / 'config' / 'default.yaml'))
    ap.add_argument('--max-index', type=int, default=3)
    ap.add_argument('--no-cameras', action='store_true')
    ap.add_argument('--no-robot', action='store_true')
    ap.add_argument('--full', action='store_true',
                    help='also run the exact preflight() the Web「自检」button uses')
    ap.add_argument('--reserve-minutes', type=float, default=20.0)
    args = ap.parse_args()

    print('=== 相机 index 扫描（开一次设备抓一帧后释放）===')
    if args.no_cameras:
        print('  skipped')
    else:
        t0 = time.perf_counter()
        scan = scan_cameras(args.max_index, 'dshow')
        for e in scan['entries']:
            if e.get('opened'):
                print(f"  index {e['index']}: {e['width']}x{e['height']} "
                      f"@{e['fps']} frame_read={e.get('frame_read')}")
            else:
                print(f"  index {e['index']}: 打不开")
        print(f"  可用 index: {scan['found']}（{time.perf_counter() - t0:.1f}s）")

    print('=== 配置里的相机源 vs 实际可用的 index ===')
    info = config_cameras(args.config)
    print(f"  绑定: {info['bindings']}")
    for cam in info['cameras']:
        print(f"  {cam['name']}: arm={cam['arm_id']} index={cam['camera_index']} "
              f"{cam['width']}x{cam['height']} enabled={cam['enabled']}")

    print('=== PLC / ADS 链路 ===')
    if args.no_robot:
        print('  skipped')
    else:
        import yaml
        cfg = yaml.safe_load(Path(args.config).read_text(encoding='utf-8'))
        twincats = [s for s in cfg.get('sources', [])
                    if s.get('type') == 'twincat' and s.get('enabled') is not False]
        if not twincats:
            print('  配置里没有启用的 twincat 源')
        for src in twincats:
            t0 = time.perf_counter()
            res = _robot_check(src)
            print(f"  {'PASS' if res['ok'] else 'FAIL'} {res['detail']}"
                  f"（{time.perf_counter() - t0:.1f}s）")

    print('=== 磁盘 ===')
    import shutil
    base = info.get('base_dir') or './sessions'
    p = Path(base)
    if not p.is_absolute():
        p = (REPO / p).resolve()
    probe = p
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        u = shutil.disk_usage(str(probe))
        print(f'  {probe} 剩余 {u.free / 2**30:.1f} GB / {u.total / 2**30:.1f} GB')
    except OSError as e:
        print('  无法读取:', e)

    if args.full:
        print('=== 完整预检（与 Web「自检」完全一致）===')
        name = suggest_session_name()
        pf = preflight(args.config, args.reserve_minutes, name,
                       not args.no_cameras, not args.no_robot)
        print(f'  会话名: {name}')
        print(f"  结论: ok={pf['ok']} failed={pf['failed']}")
        for c in pf['checks']:
            print(f"    {c['status']:<4} {c['label']}: {c['detail']}")
        print(f"  预估: {pf['estimate']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
