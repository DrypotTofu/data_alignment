"""T15 v0 web-platform overall regression (multi-session sequential review).

Runs against a live web server (default http://127.0.0.1:8765) and exercises
the full review workflow end-to-end, including the PyQt5 parity guarantees:

  A. session discovery / info
  B. alignment motion data (points / stride / NaN-free JSON)
  C. JPEG frame service (single + batch, error paths)
  D. annotations round-trip + validation (status / trim / segments,
     incl. segment-row bounds) + **byte-identical output vs the PyQt5
     writer** (approved / rejected / pending, same write path)
  E. review queue + manifest sequential flow (save -> queue shrinks ->
     all reviewed -> queue empty; manifest format parity with PyQt5)
  F. misc (task-hierarchy, index, openapi)

Test artifacts (annotations.json, review_manifest.json) are restored to the
pre-test state at the end.

Usage:
    python scripts/test/verification/web_regression.py [--base http://127.0.0.1:8765] [--keep]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SESSIONS_ROOT = REPO_ROOT / 'sessions'

PASS, FAIL = 'PASS', 'FAIL'
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    _results.append((name, PASS if ok else FAIL, detail))
    print(f'  [{PASS if ok else FAIL}] {name}' + (f' — {detail}' if detail else ''))


def _req(method: str, base: str, path: str, payload=None) -> tuple[int, dict | bytes, dict]:
    url = base + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            try:
                return r.status, json.loads(body), hdrs
            except ValueError:
                return r.status, body, hdrs
    except urllib.error.HTTPError as e:
        body = e.read()
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        try:
            return e.code, json.loads(body), hdrs
        except ValueError:
            return e.code, body, hdrs


def get(base, path): return _req('GET', base, path)
def post(base, path, payload=None): return _req('POST', base, path, payload)
def put(base, path, payload): return _req('PUT', base, path, payload)


def find_aligned_session(base: str) -> str:
    _, body, _ = get(base, '/api/sessions')
    for s in body['sessions']:
        if s.get('n_rows', 0) > 0:
            return s['name']
    raise SystemExit('no session with alignment table found')


def expected_pyqt_annotations(saved: dict, status: str, trim: dict,
                              segments: list, notes: str) -> bytes:
    """Replicate the PyQt5 writer (review_session.save_annotations +
    Path.write_text) byte-for-byte."""
    from vlgp.review.review_session import os_user as qt_user
    pyqt = {
        'version': 1,
        'reviewed_by': qt_user(),
        'reviewed_at': saved['reviewed_at'],
        'status': status,
        'reject_reason': '' if status != 'rejected' else 'rejected in review',
        'trim': trim,
        'segments': segments,
        'flags': [],
        'notes': notes,
    }
    ref = REPO_ROOT / 'var' / 'tmp_pyqt_ref.json'
    ref.write_text(json.dumps(pyqt, ensure_ascii=False, indent=2), encoding='utf-8')
    return ref.read_bytes()


def section(base: str, name: str) -> None:
    print(f'\n=== {name} ===')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8765')
    parser.add_argument('--keep', action='store_true', help='keep test artifacts')
    args = parser.parse_args()
    base = args.base.rstrip('/')

    session = find_aligned_session(base)
    print(f'regression session: {session}')

    # snapshot pre-test state
    ann_path = SESSIONS_ROOT / session / 'annotations.json'
    manifest_path = SESSIONS_ROOT / 'review_manifest.json'
    had_ann = ann_path.exists()
    had_manifest = manifest_path.exists()
    ann_backup = ann_path.read_bytes() if had_ann else None
    manifest_backup = manifest_path.read_text(encoding='utf-8') if had_manifest else None
    if had_ann:
        ann_path.unlink()
    if had_manifest:
        manifest_path.unlink()

    # ------------------------------------------------------------- A. discovery
    section(base, 'A. session discovery / info')
    s, body, _ = get(base, '/api/sessions')
    check('list sessions', s == 200 and len(body['sessions']) >= 1, f'{len(body["sessions"])} sessions')
    s, body, _ = get(base, f'/api/sessions/{session}')
    check('session info', s == 200 and body['n_rows'] > 0 and 'cameras' in body, f"n_rows={body['n_rows']}")
    s, _, _ = get(base, '/api/sessions/no_such_session_xyz')
    check('unknown session 404', s == 404)

    # ------------------------------------------------------------- B. alignment
    section(base, 'B. alignment motion data')
    s, body, _ = get(base, f'/api/sessions/{session}/alignment?points=2000')
    ok = (s == 200 and body['stride'] == 1 and len(body['rows']) == body['n_rows']
          and set(body['dpos']) == {'arm1', 'arm2', 'arm3', 'arm4'})
    check('points=2000 full res', ok, f"stride={body.get('stride')} samples={len(body.get('rows', []))}")
    s, body, _ = get(base, f'/api/sessions/{session}/alignment?points=50')
    check('points=50 downsample', s == 200 and len(body['rows']) <= 50,
          f"stride={body.get('stride')} samples={len(body.get('rows', []))}")
    s, body, _ = get(base, f'/api/sessions/{session}/alignment?stride=100')
    check('stride=100', s == 200 and len(body['rows']) == (body['n_rows'] + 99) // 100,
          f"samples={len(body.get('rows', []))}")
    s, body, _ = get(base, '/api/sessions/no_such_session_xyz/alignment')
    check('alignment 404', s == 404)

    # --------------------------------------------------------------- C. frames
    section(base, 'C. JPEG frame service')
    s, raw, hdrs = get(base, f'/api/sessions/{session}/frames/camera_arm_2/10?max_width=320')
    ok = s == 200 and hdrs.get('content-type') == 'image/jpeg' and raw[:2] == b'\xff\xd8'
    check('single frame jpeg', ok, f"{len(raw)} bytes")
    s, raw, _ = get(base, f'/api/sessions/{session}/frames/camera_arm_1/10?max_width=1')
    check('tiny frame', s == 200 and len(raw) < 4000, f"{len(raw)} bytes")
    s, _, _ = get(base, f'/api/sessions/{session}/frames/camera_arm_2/999999')
    check('frame row out of range 404', s == 404)
    s, _, _ = get(base, f'/api/sessions/{session}/frames/camera_bogus/0')
    check('unknown camera 404', s == 404)
    s, body, _ = post(base, f'/api/sessions/{session}/frames/batch',
                      {'cam': 'camera_arm_3', 'rows': [10, 11, 12], 'max_width': 160})
    ok = s == 200 and len(body['images']) == 3 and all(v.startswith('data:image/jpeg;base64,') for v in body['images'].values())
    check('batch frames', ok, f"{len(body.get('images', {}))} images")
    s, body, _ = post(base, f'/api/sessions/{session}/frames/batch',
                      {'cam': 'camera_arm_2', 'rows': [], 'max_width': 160})
    check('batch empty rows', s == 200 and body['images'] == {})

    # ---------------------------------------------------------- D. annotations
    section(base, 'D. annotations round-trip + validation + PyQt5 byte parity')
    s, body, _ = get(base, f'/api/sessions/{session}/annotations')
    check('default annotations schema', s == 200 and set(body) >= {'version', 'status', 'trim', 'segments', 'flags', 'notes'})

    trim = {'start_row': 100, 'end_row': 800}
    for status in ('approved', 'rejected', 'pending'):
        payload = {'version': 1, 'reviewed_by': '', 'reviewed_at': '', 'status': status,
                   'reject_reason': 'rejected in review' if status == 'rejected' else '',
                   'trim': trim, 'segments': [], 'flags': [], 'notes': 'regression'}
        s, body, _ = put(base, f'/api/sessions/{session}/annotations', payload)
        saved = body['saved'] if isinstance(body, dict) else {}
        ok = s == 200 and saved.get('reviewed_by') and saved.get('reviewed_at')
        if ok:
            got = ann_path.read_bytes()
            exp = expected_pyqt_annotations(saved, status, trim, [], 'regression')
            ok = got == exp
        check(f'put {status} + byte-identical', ok,
              f"bytes={ann_path.stat().st_size}" if ok else f"resp={body}")
    # round-trip
    s, body, _ = get(base, f'/api/sessions/{session}/annotations')
    check('annotations round-trip', s == 200 and body['trim'] == trim and body['notes'] == 'regression')

    # validation errors
    bad = [('invalid status', dict(payload, status='weird')),
           ('rejected w/o reason', dict(payload, status='rejected', reject_reason='')),
           ('trim out of range', dict(payload, trim={'start_row': 0, 'end_row': 999999})),
           ('trim inverted', dict(payload, trim={'start_row': 500, 'end_row': 100})),
           ('segment bad path', dict(payload, segments=[{'start_row': 10, 'end_row': 20, 'path': ['needle_passing', 'nope'], 'instruction': 'x'}])),
           ('segment row out of range', dict(payload, segments=[{'start_row': 10, 'end_row': 20, 'path': ['needle_passing', 'approach'], 'instruction': '靠近持针位置'}])),
           ('segment free text', dict(payload, segments=[{'start_row': 10, 'end_row': 20, 'path': ['needle_passing', 'approach'], 'instruction': '自由文本'}])),
           ]
    for name, pl in bad:
        if 'segment row out of range' in name:
            _, info, _ = get(base, f'/api/sessions/{session}')
            pl['segments'][0]['end_row'] = info['n_rows'] + 5
        s, body, _ = put(base, f'/api/sessions/{session}/annotations', pl)
        check(f'422: {name}', s == 422, '')

    # valid segment (vocabulary template + in-range rows)
    s, info, _ = get(base, f'/api/sessions/{session}')
    seg = {'start_row': 10, 'end_row': min(20, info['n_rows'] - 1),
           'path': ['needle_passing', 'approach'], 'instruction': '靠近持针位置', 'corrective': False}
    s, body, _ = put(base, f'/api/sessions/{session}/annotations', dict(payload, segments=[seg]))
    ok = s == 200 and body['saved']['segments'] == [seg]
    check('valid segment accepted', ok, '')
    # byte parity with segments
    got = ann_path.read_bytes()
    exp = expected_pyqt_annotations(body['saved'], 'pending', trim, [seg], 'regression')
    check('segment byte-identical', got == exp, '')

    # no-alignment session annotations -> 422
    noalign = None
    _, sess_list, _ = get(base, '/api/sessions')
    for x in sess_list['sessions']:
        if x.get('n_rows', 0) == 0 and x.get('has_alignment') is False:
            noalign = x['name']
            break
    if noalign:
        s, body, _ = put(base, f'/api/sessions/{noalign}/annotations', payload)
        check('no-alignment session 422', s == 422, '')

    # ------------------------------------------------- E. queue / manifest flow
    section(base, 'E. review queue + manifest sequential flow')
    _, body, _ = get(base, '/api/review/queue')
    pending0 = body['pending']
    check('queue initial pending', session in pending0, f"n={len(pending0)}")
    s, body, _ = put(base, f'/api/sessions/{session}/annotations', dict(payload, status='approved'))
    check('review approved', s == 200, '')
    _, body, _ = get(base, '/api/review/queue')
    check('queue excludes reviewed', session not in body['pending'],
          f"pending now {body['pending']}")
    s, manifest, _ = get(base, '/api/review/manifest')
    entry = manifest.get(session, {})
    check('manifest entry', s == 200 and entry.get('status') == 'approved'
          and entry.get('has_annotations') is True
          and set(entry) == {'status', 'reviewed_at', 'has_annotations'},
          str(entry))
    # manifest byte format parity (PyQt5 _update_manifest: status, reviewed_at, has_annotations)
    m_bytes = manifest_path.read_bytes()
    pyqt_manifest = {session: {'status': 'approved', 'reviewed_at': entry['reviewed_at'],
                               'has_annotations': True}}
    ref = REPO_ROOT / 'var' / 'tmp_manifest_ref.json'
    ref.write_text(json.dumps(pyqt_manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    check('manifest byte-identical (PyQt5 format)', m_bytes == ref.read_bytes(), '')
    # review all pending -> queue empty
    for name in list(pending0):
        if name == session:
            continue
        s, body, _ = put(base, f'/api/sessions/{name}/annotations',
                         {'version': 1, 'reviewed_by': '', 'reviewed_at': '', 'status': 'rejected',
                          'reject_reason': '', 'trim': {'start_row': 0, 'end_row': 0},
                          'segments': [], 'flags': [], 'notes': 'regression'})
        check(f'review {name} (rejected)', s == 200, '')
    _, body, _ = get(base, '/api/review/queue')
    check('queue empty after all reviewed', body['n_pending'] == 0, f"pending={body['pending']}")

    # --------------------------------------------------------------- F. misc
    section(base, 'F. misc')
    s, body, _ = get(base, '/api/task-hierarchy')
    check('task hierarchy', s == 200 and body['status'] == 'placeholder' and len(body['all_paths']) >= 15)
    s, raw, _ = get(base, '/')
    check('index html', s == 200 and b'VLGP' in raw)
    s, raw, _ = get(base, '/openapi.json')
    check('openapi', s == 200)

    # ------------------------------------------ G. on-demand alignment (safe subset)
    section(base, 'G. on-demand alignment (POST /align)')
    s, body, _ = post(base, f'/api/sessions/{session}/align')
    check('already-aligned idempotent', s == 200 and body.get('status') == 'already_aligned'
          and body.get('n_rows') == body.get('n_rows', 0) > 0, str(body.get('status')))
    s, body, _ = post(base, '/api/sessions/no_such_session_xyz/align')
    check('align unknown session 404', s == 404)
    if noalign:
        s, body, _ = post(base, f'/api/sessions/{noalign}/align')
        detail = body.get('detail') if isinstance(body, dict) else None
        ok = s == 422 and isinstance(detail, dict) and 'error' in detail
        check('align no-robot-data 422', ok, str(body)[:80])
    # note: full generation path (unaligned -> generated, byte-identical to
    # the CLI) is exercised manually; it requires moving session data.

    # ------------------------------------------------------------ cleanup
    if not args.keep:
        if had_ann and ann_backup is not None:
            ann_path.write_bytes(ann_backup)
        else:
            ann_path.unlink(missing_ok=True)
        if had_manifest and manifest_backup is not None:
            manifest_path.write_text(manifest_backup, encoding='utf-8')
        else:
            manifest_path.unlink(missing_ok=True)
        print('\n(test artifacts restored)')

    # -------------------------------------------------------------- summary
    n_pass = sum(1 for _, st, _ in _results if st == PASS)
    n_fail = len(_results) - n_pass
    print(f'\n===== regression: {n_pass}/{len(_results)} PASS =====')
    for name, st, detail in _results:
        if st == FAIL:
            print(f'  FAILED: {name} {detail}')
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
