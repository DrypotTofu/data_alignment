# -*- coding: utf-8 -*-
"""Bisect which spawn option makes the camera worker fail.

Observed: the acquisition started from the Web service loses the camera (worker
produces no output, dies after the 20 s ready timeout), while the very same CLI
started from a shell works (319 frames in 14 s). This probe starts the
acquisition from a plain process with the service's exact Popen options and then
drops them one at a time.

Needs camera access (DSH harness: danger-full-access).

Usage: python scripts/test/probes/probe_spawn_like_service.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LOG_DIR = REPO / 'var' / 'spawn_probe'
CONFIG = REPO / 'scripts' / 'config' / 'arm2_only.yaml'


def run(tag: str, *, pipe_stdin: bool, new_group: bool, text: bool,
        stdin_eof_flag: bool = False, run_s: float = 12.0) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stop = LOG_DIR / f'{tag}.stop'
    stop.unlink(missing_ok=True)
    log = LOG_DIR / f'{tag}.log'
    cmd = [sys.executable, '-u', '-m', 'vlgp.capture.manual_acquisition',
           '--config', str(CONFIG), '--name', f'spawn_{tag}',
           '--stop-file', str(stop)]
    if stdin_eof_flag:
        cmd.append('--stop-on-stdin-eof')
    kwargs = {}
    if pipe_stdin:
        kwargs['stdin'] = subprocess.PIPE
    else:
        kwargs['stdin'] = subprocess.DEVNULL
    if new_group and sys.platform == 'win32':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    if text:
        kwargs.update(text=True, encoding='utf-8', errors='replace')
    t0 = time.time()
    with open(log, 'w', encoding='utf-8') as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, **kwargs)
        time.sleep(run_s)
        sess = REPO / 'sessions' / f'spawn_{tag}' / 'camera_arm_2'
        pngs_mid = len(list(sess.glob('*.png'))) if sess.is_dir() else 0
        stop.write_text('stop\n', encoding='utf-8')
        try:
            proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            proc.kill()
    pngs = len(list(sess.glob('*.png'))) if sess.is_dir() else 0
    text_log = log.read_text(encoding='utf-8', errors='replace')
    ready = 'Worker camera_arm_2 is ready' in text_log
    opened = 'Worker opened camera' in text_log
    err = [ln for ln in text_log.splitlines()
           if 'not opening' in ln or 'Cannot open' in ln or 'RuntimeError' in ln]
    return {'tag': tag, 'ready': ready, 'worker_opened': opened,
            'pngs_mid': pngs_mid, 'pngs': pngs,
            'elapsed': round(time.time() - t0, 1),
            'errors': err[:2], 'log': str(log)}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--variants', default='',
                    help='comma-separated subset, e.g. S5_stdin_eof_flag,S1_service_kwargs')
    args = ap.parse_args()
    variants = [
        ('S1_service_kwargs', dict(pipe_stdin=True, new_group=True, text=True)),
        ('S2_no_new_group', dict(pipe_stdin=True, new_group=False, text=True)),
        ('S3_no_stdin_pipe', dict(pipe_stdin=False, new_group=True, text=True)),
        ('S4_plain', dict(pipe_stdin=False, new_group=False, text=False)),
        # the service also adds --stop-on-stdin-eof when it could create the pipe
        ('S5_stdin_eof_flag', dict(pipe_stdin=True, new_group=True, text=True,
                                   stdin_eof_flag=True)),
    ]
    sel = [v for v in variants
           if not args.variants or v[0] in args.variants.split(',')]
    if not sel:
        raise SystemExit(f'no matching variant in {args.variants!r}')
    results = []
    for tag, kw in sel:
        print(f'=== {tag}: {kw} ===', flush=True)
        r = run(tag, **kw)
        results.append(r)
        print(f"  worker_opened={r['worker_opened']} ready={r['ready']} "
              f"pngs={r['pngs']} (mid={r['pngs_mid']}) [{r['elapsed']}s]", flush=True)
        for e in r['errors']:
            print('   !', e[:140])
    print('\nsummary:')
    for r in results:
        print(f"  {r['tag']:<20} opened={r['worker_opened']} ready={r['ready']} "
              f"pngs={r['pngs']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
