# -*- coding: utf-8 -*-
"""End-to-end test of the IN/OUT-aware video export (web pipeline kind=video).

Covers the three branches of POST /api/sessions/{name}/pipeline/video:
  1. annotations carry a trim       -> per-camera --start-sec/--end-sec
  2. no trim, no confirm            -> HTTP 409 warning, nothing started
  3. no trim + ?confirm=1           -> full preview export

The probe backs up annotations.json / review_manifest.json and deletes the
videos it produces, so the session is left exactly as found.

Run (server must be listening on 8766):
    python scripts/test/probes/probe_trim_export.py
"""
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8766'
REPO = Path(__file__).resolve().parents[3]
SESSION = 'test_20260906_113038'
SESS_DIR = REPO / 'sessions' / SESSION
BACKUP = REPO / 'var' / 'probe_trim_backup'
TRIM = {'start_row': 10, 'end_row': 30}


def req(method: str, path: str, body=None, timeout: float = 300,
        raw: bool = False):
    """HTTP request; returns (status, decoded body or raw bytes)."""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:      # 409 warning path lands here
        payload, status = e.read(), e.code
    if raw:
        return status, payload
    try:
        return status, json.loads(payload)
    except ValueError:
        return status, payload.decode('utf-8', 'replace')


def text(body) -> str:
    """Log endpoint returns text/plain -> urllib hands back raw bytes."""
    return body.decode('utf-8', 'replace') if isinstance(body, bytes) else str(body)


def wait_job(job_id: str, timeout_s: float = 300) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        _, st = req('GET', f'/api/pipeline/{job_id}/status')
        if st['status'] != 'running':
            return st
        time.sleep(0.5)
    raise TimeoutError(job_id)


def backup() -> None:
    BACKUP.mkdir(parents=True, exist_ok=True)
    for name in ('annotations.json', 'review_manifest.json'):
        src = (SESS_DIR / name) if name == 'annotations.json' \
            else (REPO / 'sessions' / name)
        dst = BACKUP / (f'{SESSION}__{name}' if name == 'annotations.json' else name)
        if src.exists():
            shutil.copy2(src, dst)
            print(f'  backed up {src} -> {dst}')
        else:
            print(f'  {src} absent (nothing to back up)')


def restore() -> None:
    for name, src in (('annotations.json', SESS_DIR / 'annotations.json'),
                      ('review_manifest.json', REPO / 'sessions' / 'review_manifest.json')):
        dst = BACKUP / (f'{SESSION}__{name}' if name == 'annotations.json' else name)
        if dst.exists():
            shutil.copy2(dst, src)
            print(f'  restored {src}')
        elif src.exists():
            src.unlink()
            print(f'  removed probe-created {src}')
    for cam in ('1', '2', '3'):
        p = SESS_DIR / f'arm{cam}_preview.mp4'
        if p.exists():
            p.unlink()
            print(f'  removed {p.name}')


def put_annotations(trim) -> dict:
    _, ann = req('GET', f'/api/sessions/{SESSION}/annotations')
    ann = dict(ann)
    ann['trim'] = trim
    ann['status'] = 'pending'
    ann.pop('reviewed_by', None)
    ann.pop('reviewed_at', None)
    status, saved = req('PUT', f'/api/sessions/{SESSION}/annotations', ann)
    assert status == 200, (status, saved)
    return saved['saved']


def main() -> int:
    ok = True
    avails = [c for c in ('camera_arm_1', 'camera_arm_2', 'camera_arm_3')
              if (SESS_DIR / c / 'metadata.csv').exists()]
    print(f'session: {SESSION}  rows={len(pd_rows())}  cameras={avails}')
    backup()
    try:        # ---------------------------------------------------- 1. trim export
        print('=== 1. IN/OUT trim -> cropped export ===')
        put_annotations(TRIM)
        status, job = req('POST', f'/api/sessions/{SESSION}/pipeline/video')
        assert status == 200, (status, job)
        print('  job:', job['id'], '|', job['label'])
        st = wait_job(job['id'])
        _, log = req('GET', f'/api/pipeline/{job["id"]}/log')
        log = text(log)
        print('  exit:', st['exit_code'], '| label:', st['label'])
        cmds = [ln for ln in log.splitlines() if ln.startswith('=== ')]
        for ln in cmds:
            print('   ', ln)
        if st['exit_code'] != 0:
            print('  --- job log ---')
            print(log)
            ok = False
        assert st['label'] == '导出裁剪区间视频', st
        n_trim = sum('--start-sec' in ln and '--end-sec' in ln for ln in cmds)
        print(f'  commands: {len(cmds)} (expected {len(avails)}),'
              f' with --start-sec/--end-sec: {n_trim}')
        ok &= len(cmds) == len(avails) and n_trim == len(avails)
        # the OUT row must be INSIDE the exported range (inclusive semantics,
        # same as build_srth_dataset's `start_row <= row <= end_row`)
        seg = [ln for ln in log.splitlines() if 'Segment [' in ln]
        for ln in seg:
            print('   ', ln.strip())
        ok &= bool(seg) and all(f'idx {TRIM["start_row"]}..{TRIM["end_row"]}' in ln
                                for ln in seg)
        for cam in avails:
            arm = cam.rsplit('_', 1)[-1]
            p = SESS_DIR / f'arm{arm}_preview.mp4'
            print(f'  arm{arm}_preview.mp4: exists={p.exists()}'
                  f' size={p.stat().st_size if p.exists() else 0}')
            ok &= p.exists() and p.stat().st_size > 0
        skipped = [c for c in ('camera_arm_1', 'camera_arm_2', 'camera_arm_3')
                   if c not in avails]
        if skipped:
            ok &= any('跳过无 metadata.csv 的相机' in ln and skipped[0] in ln
                      for ln in log.splitlines())

        # ------------------------------------------- 2. no trim, no confirm
        print('=== 2. no trim, no confirm -> 409 warning ===')
        put_annotations(None)
        status, body = req('POST', f'/api/sessions/{SESSION}/pipeline/video')
        print('  HTTP', status, '|', body)
        ok &= status == 409
        ok &= isinstance(body, dict) and body.get('detail', {}).get('warning') is True

        # ------------------------------------------- 3. no trim + confirm=1
        print('=== 3. no trim + confirm=1 -> full export ===')
        status, job = req('POST', f'/api/sessions/{SESSION}/pipeline/video?confirm=1')
        assert status == 200, (status, job)
        print('  job:', job['id'], '|', job['label'])
        st = wait_job(job['id'])
        _, log = req('GET', f'/api/pipeline/{job["id"]}/log')
        log = text(log)
        cmds = [ln for ln in log.splitlines() if ln.startswith('=== ')]
        n_trim = sum('--start-sec' in ln for ln in cmds)
        print('  exit:', st['exit_code'], '| label:', st['label'],
              '| commands with trim args:', n_trim)
        ok &= st['exit_code'] == 0 and st['label'] == '导出预览视频(全部)'
        ok &= n_trim == 0

        # ------------------------------- 4. synthetic 3-camera session
        ok &= synth_3cam_section()
    finally:
        restore()
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


def synth_3cam_section() -> bool:
    """Full 3-camera IN/OUT export on the synthetic fixture session.

    No real session on disk has wrist-camera data, so this is the only coverage
    of the per-camera (exact) trim mapping and of a 3-camera export sequence.
    The fixture's three cameras start at different times, so grid-relative
    seconds would be visibly wrong per camera.
    """
    import sys
    sys.path.insert(0, str(REPO / 'scripts' / 'test' / 'fixtures'))
    from build_fixture_session import (CAM_SPEC, SESSION as SYNTH, STEP_NS,
                                       build, frame_times, nearest_idx)
    root = REPO / 'var' / 'test_fixtures' / 'synth_3cam'
    sess_dir = build(root, 150)
    print('=== 4. synthetic 3-camera session (fixture) ===')
    ok = True
    _, before = req('GET', '/api/settings')
    orig_root = before['root']
    try:
        st, sw = req('PUT', '/api/settings/root', {'path': str(root)})
        print(f'  root -> {sw.get("root")} (HTTP {st}, {sw.get("n_sessions")} session)')
        ok &= st == 200 and Path(sw['root']) == root
        _, info = req('GET', f'/api/sessions/{SYNTH}')
        print(f'  rows={info["n_rows"]}  cameras={info["cameras"]}')
        ok &= info['n_rows'] == 150 and all(info['cameras'].values())

        trim = {'start_row': 20, 'end_row': 60}
        _, ann = req('GET', f'/api/sessions/{SYNTH}/annotations')
        ann = dict(ann, trim=trim, status='pending')
        st, _ = req('PUT', f'/api/sessions/{SYNTH}/annotations', ann)
        ok &= st == 200

        st, job = req('POST', f'/api/sessions/{SYNTH}/pipeline/video')
        st2 = wait_job(job['id'])
        _, log = req('GET', f'/api/pipeline/{job["id"]}/log')
        log = text(log)
        print(f'  job {job["id"]}: exit={st2["exit_code"]} label={st2["label"]}')
        ok &= st2['exit_code'] == 0 and st2['label'] == '导出裁剪区间视频'
        cmds = [ln for ln in log.splitlines() if ln.startswith('=== ')]
        ok &= len(cmds) == 3
        # expected per-camera seconds come from the fixture's own timing model
        anchor_t = frame_times('camera_arm_2', 150)
        for cam in ('camera_arm_2', 'camera_arm_1', 'camera_arm_3'):
            times = frame_times(cam, 150 + CAM_SPEC[cam][1])
            js = nearest_idx(times, anchor_t[trim['start_row']])
            je = nearest_idx(times, anchor_t[trim['end_row']])
            exp_s = (times[js] - times[0]) / 1e9
            exp_e = (times[je] - times[0]) / 1e9
            line = next((c for c in cmds if f'--dir {sess_dir / cam} ' in c + ' '), '')
            got_s = _arg(line, '--start-sec')
            got_e = _arg(line, '--end-sec')
            good = (got_s is not None and abs(got_s - exp_s) < 1e-5
                    and got_e is not None and abs(got_e - exp_e) < 1e-5)
            ok &= good
            print(f'  {cam}: start={got_s} (want {exp_s:.6f}) '
                  f'end={got_e} (want {exp_e:.6f}) {"OK" if good else "MISMATCH"}')
            # frame count must cover BOTH IN and OUT rows (inclusive)
            mp4 = sess_dir / f'arm{cam[-1]}_preview.mp4'
            n = _mp4_frames(mp4)
            want = trim['end_row'] - trim['start_row'] + 1
            ok &= n == want
            print(f'    {mp4.name}: {n} frames (want {want}), exists={mp4.exists()}')
        # the mapping must not be the grid fallback for all cameras
        grid = trim['start_row'] / 30.0
        starts = [_arg(c, '--start-sec') for c in cmds]
        ok &= len({round(s, 5) for s in starts if s is not None}) == 3
        ok &= all(abs(s - grid) > 1e-4 for s in starts if s is not None)
        print(f'  per-camera starts differ: {[round(s, 4) for s in starts if s]}'
              f' (grid fallback would be {grid:.4f})')

        # wrist frames + 3-camera preload on the same fixture
        for cam in ('camera_arm_1', 'camera_arm_3'):
            st, data = req('GET', f'/api/sessions/{SYNTH}/frames/{cam}/25?max_width=32',
                           raw=True)
            ok &= st == 200 and data[:2] == b'\xff\xd8'
            print(f'  frame {cam}/25: HTTP {st}, {len(data)} bytes')
        _, pr = req('POST', f'/api/sessions/{SYNTH}/preload')
        pst = pr
        for _ in range(60):
            _, pst = req('GET', f'/api/sessions/{SYNTH}/preload/status')
            if pst['status'] != 'running':
                break
            time.sleep(0.5)
        print(f'  preload: {pst}')
        ok &= pst['status'] == 'done' and pst['error'] is None
        ok &= sorted(pst.get('cams', [])) == ['camera_arm_1', 'camera_arm_2',
                                              'camera_arm_3']
        ok &= pst['current'] == pst['total']
        # cleanup this session's artifacts
        for cam in ('1', '2', '3'):
            (sess_dir / f'arm{cam}_preview.mp4').unlink(missing_ok=True)
        (sess_dir / 'annotations.json').unlink(missing_ok=True)
    finally:
        req('PUT', '/api/settings/root', {'path': orig_root})
    return ok


def _arg(line: str, flag: str) -> float | None:
    parts = line.split()
    if flag in parts:
        try:
            return float(parts[parts.index(flag) + 1])
        except (IndexError, ValueError):
            return None
    return None


def _mp4_frames(path: Path) -> int:
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = 0
    while True:
        good, _ = cap.read()
        if not good:
            break
        n += 1
    cap.release()
    return n


def pd_rows() -> list:
    import csv
    with open(SESS_DIR / 'camera_anchor_alignment' / 'camera_anchor_alignment.csv',
              encoding='utf-8') as f:
        return list(csv.DictReader(f))


if __name__ == '__main__':
    raise SystemExit(main())
