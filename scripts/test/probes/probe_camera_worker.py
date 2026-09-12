# -*- coding: utf-8 -*-
"""Run the acquisition camera worker directly, with variants, to find out why it
cannot open index 0 (the session log said "camera index 0 not opening").

The worker is exactly what the acquisition spawns for a `process_camera` source:
    python -m vlgp.acquisition.process_workers.usb_camera_worker --index 0 ...
It signals readiness with a `.ready` file and stops on a `.stop` file, so this
probe can drive it like the real session does and report frames + stderr.

Needs camera access (DSH harness: danger-full-access — the confined sandbox hides
UVC devices).

Usage:
    python scripts/test/probes/probe_camera_worker.py               # config params (MJPG/fps 60)
    python scripts/test/probes/probe_camera_worker.py --no-fourcc --no-fps
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO / 'var' / 'camera_worker_probe'


def run_variant(tag: str, *, index: int, width: int, height: int, fps: int | None,
                fourcc: str | None, target_fps: int, backend: str,
                image_format: str, run_s: float, warmup_s: float,
                png_compression: int = 1, writer_threads: int = 6) -> dict:
    out = OUT_ROOT / tag
    if out.exists():
        import shutil
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    stop_file = out / '.stop'
    ready_file = out / '.ready'
    argv = [sys.executable, '-u', '-m',
            'vlgp.acquisition.process_workers.usb_camera_worker',
            '--index', str(index), '--topic', 'camera/arm_2/image_raw',
            '--out-dir', str(out), '--backend', backend,
            '--image-format', image_format,
            '--stop-file', str(stop_file), '--ready-file', str(ready_file),
            '--source-name', 'camera_arm_2', '--clock-offset-ns', '0',
            '--width', str(width), '--height', str(height),
            '--target-fps', str(target_fps),
            '--png-compression', str(png_compression),
            '--writer-threads', str(writer_threads),
            '--warmup-s', str(warmup_s)]
    if fps is not None:
        argv += ['--fps', str(fps)]
    if fourcc:
        argv += ['--fourcc', fourcc]
    log = (REPO / 'var' / 'test_fixtures' / f'worker_{tag}.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, 'w', encoding='utf-8') as lf:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=lf,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', cwd=str(REPO))
        t0 = time.time()
        ready = False
        while time.time() - t0 < 25:
            if ready_file.exists():
                ready = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        ready_s = time.time() - t0
        frames_at_ready = len(list(out.glob('*.png')))
        mid = None
        if ready:
            time.sleep(run_s)
            mid = len(list(out.glob('*.png')))
            stop_file.write_text('stop\n', encoding='utf-8')
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = 'killed'
    frames = len(list(out.glob('*.png')))
    size = sum(p.stat().st_size for p in out.glob('*.png'))
    text = log.read_text(encoding='utf-8', errors='replace')
    return {'tag': tag, 'ready': ready, 'ready_s': round(ready_s, 1),
            'exit': code, 'frames': frames, 'files_at_ready': frames_at_ready,
            'files_mid': mid, 'mb': round(size / 1048576, 1), 'log': str(log),
            'log_tail': '\n'.join(text.splitlines()[-12:])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', type=int, default=0)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--fps', type=int, default=60)
    ap.add_argument('--fourcc', default='MJPG')
    ap.add_argument('--no-fps', action='store_true', help='omit --fps (do not touch CAP_PROP_FPS)')
    ap.add_argument('--no-fourcc', action='store_true', help='omit --fourcc')
    ap.add_argument('--target-fps', type=int, default=30)
    ap.add_argument('--backend', default='dshow')
    ap.add_argument('--image-format', default='png')
    ap.add_argument('--run-s', type=float, default=8.0)
    ap.add_argument('--warmup-s', type=float, default=1.5)
    args = ap.parse_args()

    variants = [('as_configured',
                 dict(fps=None if args.no_fps else args.fps,
                      fourcc=None if args.no_fourcc else args.fourcc))]
    if not args.no_fourcc:
        variants.append(('no_fourcc', dict(fps=None if args.no_fps else args.fps,
                                           fourcc=None)))
    if not args.no_fps:
        variants.append(('no_fps', dict(fps=None, fourcc=args.fourcc)))
    variants.append(('plain', dict(fps=None, fourcc=None)))

    results = []
    for tag, over in variants:
        print(f'=== worker variant: {tag} ({over}) ===', flush=True)
        r = run_variant(tag, index=args.index, width=args.width, height=args.height,
                        target_fps=args.target_fps, backend=args.backend,
                        image_format=args.image_format, run_s=args.run_s,
                        warmup_s=args.warmup_s, **over)
        results.append(r)
        print(f"  ready={r['ready']} ({r['ready_s']}s) exit={r['exit']} "
              f"frames={r['frames']} ({r['mb']} MB) log={r['log']}")
        if not r['ready']:
            print('  --- log tail ---')
            print('  ' + r['log_tail'].replace('\n', '\n  '))
    print('\nsummary:')
    for r in results:
        print(f"  {r['tag']:<14} ready={r['ready']} frames={r['frames']} "
              f"exit={r['exit']} files_at_ready={r['files_at_ready']} "
              f"files_mid={r['files_mid']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
