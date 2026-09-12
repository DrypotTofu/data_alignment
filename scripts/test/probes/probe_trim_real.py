# -*- coding: utf-8 -*-
"""IN/OUT video export on a REAL recorded session, through the Web API.

Puts a trim into annotations.json, runs pipeline kind=video, then checks that
the exported clip covers exactly the annotated rows (end_row - start_row + 1
frames) — the per-camera mapping from §1.2 of the tech note, on real timestamps.

Usage:
    python scripts/test/probes/probe_trim_real.py [--session arm2_real_1]
        [--start 200] [--end 500] [--base http://127.0.0.1:8766]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OK = True


def check(label: str, cond: bool, extra: str = '') -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}",
          flush=True)


def req(base: str, method: str, path: str, body=None, timeout: float = 900,
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
    ap.add_argument('--session', default='arm2_real_1')
    ap.add_argument('--start', type=int, default=200)
    ap.add_argument('--end', type=int, default=500)
    ap.add_argument('--base', default='http://127.0.0.1:8766')
    args = ap.parse_args()
    base = args.base.rstrip('/')
    sess_dir = REPO / 'sessions' / args.session
    ann_path = sess_dir / 'annotations.json'
    had_ann = ann_path.exists()
    ann_backup = ann_path.read_bytes() if had_ann else None
    print(f'session: {args.session}  trim rows [{args.start}, {args.end}]')

    try:
        st, info = req(base, 'GET', f'/api/sessions/{args.session}')
        check('session visible in the web platform', st == 200,
              f"rows={info.get('n_rows') if isinstance(info, dict) else info}")
        n_rows = info['n_rows']

        st, ann = req(base, 'GET', f'/api/sessions/{args.session}/annotations')
        ann = dict(ann)
        ann['trim'] = {'start_row': args.start, 'end_row': args.end}
        ann['status'] = 'pending'
        ann.pop('reviewed_by', None)
        ann.pop('reviewed_at', None)
        st, saved = req(base, 'PUT', f'/api/sessions/{args.session}/annotations', ann)
        check('trim saved', st == 200 and saved['saved']['trim']['start_row'] == args.start,
              json.dumps(saved, ensure_ascii=False)[:120])

        st, job = req(base, 'POST', f'/api/sessions/{args.session}/pipeline/video')
        check('video job started', st == 200 and 'id' in job,
              json.dumps(job, ensure_ascii=False)[:120])
        job_id = job['id']
        t0 = time.time()
        while time.time() - t0 < 600:
            _, status = req(base, 'GET', f'/api/pipeline/{job_id}/status')
            if status.get('status') != 'running':
                break
            time.sleep(1.0)
        st, log = req(base, 'GET', f'/api/pipeline/{job_id}/log', raw=True)
        log = log.decode('utf-8', 'replace') if isinstance(log, bytes) else str(log)
        print('  job:', status.get('status'), 'exit', status.get('exit_code'),
              '|', status.get('label'))
        for line in log.splitlines():
            if line.startswith('=== ') or 'Segment [' in line or 'rows [' in line:
                print('   ', line.strip()[:150])
        check('job succeeded', status.get('status') == 'done'
              and status.get('exit_code') == 0, str(status))
        check('label = 导出裁剪区间视频', status.get('label') == '导出裁剪区间视频',
              str(status.get('label')))
        check('exact per-camera mapping used', 'exact' in log)
        check('trim seconds appear in the log',
              '--start-sec' in log and '--end-sec' in log)

        # frames: end_row is inclusive, so the clip must hold end-start+1 frames
        import cv2
        mp4 = sess_dir / 'arm2_preview.mp4'
        want = args.end - args.start + 1
        cap = cv2.VideoCapture(str(mp4))
        n = 0
        while True:
            good, _ = cap.read()
            if not good:
                break
            n += 1
        cap.release()
        check(f'clip exists with {want} frames', mp4.exists() and n == want,
              f'{n} frames, {mp4.stat().st_size / 1048576:.1f} MB' if mp4.exists() else 'missing')
        check('trim rows inside the session', 0 <= args.start <= args.end < n_rows)
    finally:
        if had_ann and ann_backup is not None:
            ann_path.write_bytes(ann_backup)
        else:
            ann_path.unlink(missing_ok=True)
        print('  (annotations restored)')
    print('RESULT:', 'PASS' if OK else 'FAIL')
    return 0 if OK else 1


if __name__ == '__main__':
    raise SystemExit(main())
