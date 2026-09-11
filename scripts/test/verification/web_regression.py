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

Data-adaptive: the suite picks the session with the most cameras (then the most
rows) because the sessions kept on disk are single-camera test recordings; checks
that need a wrist camera or a long row range are SKIPped instead of failing.
An explicit session can be forced with --session.

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

PASS, FAIL, SKIP = 'PASS', 'FAIL', 'SKIP'
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    _results.append((name, PASS if ok else FAIL, detail))
    print(f'  [{PASS if ok else FAIL}] {name}' + (f' — {detail}' if detail else ''))


def skip(name: str, why: str) -> None:
    _results.append((name, SKIP, why))
    print(f'  [{SKIP}] {name} — {why}')


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


def find_aligned_session(base: str, want: str | None = None) -> dict:
    """Preferred session info: most cameras first, then most rows.

    The platform has to work on single-camera (endoscope-only) recordings too,
    so the suite adapts to whatever is on disk instead of assuming three
    cameras and >=800 rows.
    """
    _, body, _ = get(base, '/api/sessions')
    cands = [s for s in body['sessions'] if s.get('n_rows', 0) > 0]
    if want:
        for s in cands:
            if s['name'] == want:
                return s
        raise SystemExit(f'session {want!r} has no alignment table')
    if not cands:
        raise SystemExit('no session with alignment table found')
    cands.sort(key=lambda s: (-sum(1 for v in (s.get('cameras') or {}).values() if v),
                              s['n_rows']))
    return cands[0]


def expected_pyqt_annotations(saved: dict, status: str, trim: dict,
                              segments: list, notes: str) -> bytes:
    """Replicate the PyQt5 writer (review_session.save_annotations +
    Path.write_text) byte-for-byte."""
    from vlgp.review.common import os_user as qt_user
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
    parser.add_argument('--session', default=None,
                        help='force a session by name (default: best on disk)')
    args = parser.parse_args()
    base = args.base.rstrip('/')

    info0 = find_aligned_session(base, args.session)
    session = info0['name']
    n_rows = info0['n_rows']
    cams = [c for c, ok in (info0.get('cameras') or {}).items() if ok] or ['camera_arm_2']
    # the platform can serve any data folder (see the data-root picker), so the
    # on-disk artifacts are looked up under the ACTIVE root, not repo sessions/
    _, st_body, _ = get(base, '/api/settings')
    data_root = Path(st_body['root']) if isinstance(st_body, dict) else SESSIONS_ROOT
    print(f'regression session: {session}  rows={n_rows}  cameras={cams}')
    print(f'active data root  : {data_root}')

    # snapshot pre-test state
    ann_path = data_root / session / 'annotations.json'
    manifest_path = data_root / 'review_manifest.json'
    had_ann = ann_path.exists()
    had_manifest = manifest_path.exists()
    ann_backup = ann_path.read_bytes() if had_ann else None
    manifest_backup = manifest_path.read_text(encoding='utf-8') if had_manifest else None
    if had_ann:
        ann_path.unlink()
    if had_manifest:
        manifest_path.unlink()
    # Section E reviews EVERY pending session, so snapshot each one as well —
    # otherwise the suite leaves a bogus rejected/"regression" annotations.json
    # behind, which would make build_srth_dataset skip that session.
    touched: dict[Path, bytes | None] = {ann_path: ann_backup}
    _, sess_all, _ = get(base, '/api/sessions')
    for s in sess_all['sessions']:
        p = data_root / s['name'] / 'annotations.json'
        touched.setdefault(p, p.read_bytes() if p.exists() else None)

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
    # <=2000 rows must come back at full resolution; longer sessions are
    # downsampled to the 2000-point budget (that is the endpoint's contract)
    if body.get('n_rows', 0) <= 2000:
        ok = s == 200 and body['stride'] == 1 and len(body['rows']) == body['n_rows']
    else:
        ok = (s == 200 and len(body['rows']) <= 2000
              and body['stride'] == -(-body['n_rows'] // 2000))
    ok = ok and set(body['dpos']) == {'arm1', 'arm2', 'arm3', 'arm4'}
    check('points=2000 budget', ok,
          f"stride={body.get('stride')} samples={len(body.get('rows', []))}"
          f" rows={body.get('n_rows')}")
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
    main_cam = 'camera_arm_2' if 'camera_arm_2' in cams else cams[0]
    s, raw, hdrs = get(base, f'/api/sessions/{session}/frames/{main_cam}/10?max_width=320')
    ok = s == 200 and hdrs.get('content-type') == 'image/jpeg' and raw[:2] == b'\xff\xd8'
    check('single frame jpeg', ok, f"{len(raw)} bytes")
    wrist = [c for c in cams if c != main_cam]
    if wrist:
        s, raw, _ = get(base, f'/api/sessions/{session}/frames/{wrist[0]}/10?max_width=1')
        check('tiny frame', s == 200 and len(raw) < 4000, f"{len(raw)} bytes")
    else:
        skip('tiny frame', f'{session} has no wrist camera data')
    s, _, _ = get(base, f'/api/sessions/{session}/frames/{main_cam}/999999')
    check('frame row out of range 404', s == 404)
    s, _, _ = get(base, f'/api/sessions/{session}/frames/camera_bogus/0')
    check('unknown camera 404', s == 404)
    if wrist:
        s, _, _ = get(base, f'/api/sessions/{session}/frames/{wrist[0]}/10')
        check('wrist frame served', s == 200, wrist[0])
    else:
        skip('wrist frame served', 'no wrist camera data in this session')
    # a camera that the session does not have must be a clean 404, not a 500
    absent = [c for c in ('camera_arm_1', 'camera_arm_3') if c not in cams]
    if absent:
        s, _, _ = get(base, f'/api/sessions/{session}/frames/{absent[0]}/10')
        check('absent camera 404 (not 500)', s == 404, absent[0])
    s, body, _ = post(base, f'/api/sessions/{session}/frames/batch',
                      {'cam': cams[-1], 'rows': [10, 11, 12], 'max_width': 160})
    ok = s == 200 and len(body['images']) == 3 and all(v.startswith('data:image/jpeg;base64,') for v in body['images'].values())
    check('batch frames', ok, f"{len(body.get('images', {}))} images")
    s, body, _ = post(base, f'/api/sessions/{session}/frames/batch',
                      {'cam': main_cam, 'rows': [], 'max_width': 160})
    check('batch empty rows', s == 200 and body['images'] == {})

    # ---------------------------------------------------------- D. annotations
    section(base, 'D. annotations round-trip + validation + PyQt5 byte parity')
    s, body, _ = get(base, f'/api/sessions/{session}/annotations')
    check('default annotations schema', s == 200 and set(body) >= {'version', 'status', 'trim', 'segments', 'flags', 'notes'})

    trim = {'start_row': min(100, max(0, n_rows // 4)),
            'end_row': min(800, n_rows - 1)}
    if trim['start_row'] >= trim['end_row']:
        trim = {'start_row': 0, 'end_row': max(0, n_rows - 1)}
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
    inv_lo = min(30, max(1, n_rows - 2))
    bad = [('invalid status', dict(payload, status='weird')),
           ('rejected w/o reason', dict(payload, status='rejected', reject_reason='')),
           ('trim out of range', dict(payload, trim={'start_row': 0, 'end_row': n_rows + 100})),
           ('trim inverted', dict(payload, trim={'start_row': inv_lo, 'end_row': max(0, inv_lo - 10)})),
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
                          'reject_reason': 'rejected in review',   # contract: required for rejected
                          'trim': {'start_row': 0, 'end_row': 0},
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
    # note: full generation path (unaligned -> started -> status done,
    # byte-identical to the CLI) is exercised manually; it requires moving
    # session data.

    # ----------------------------- H. align status + per-row robot status
    section(base, 'H. align status + robot row status')
    s, body, _ = get(base, f'/api/sessions/{session}/align/status')
    check('align status endpoint', s == 200 and body.get('status') in ('idle', 'done'))
    s, body, _ = get(base, f'/api/sessions/{session}/row/10')
    arms = body.get('arms', {})
    # instrument label comes from the alignment table (arm2 = endoscope code 13
    # in every recorded session); scope_position_mm is a 3-vector or None
    want_instr = None
    try:
        import pandas as pd
        align = data_root / session / 'camera_anchor_alignment' / 'camera_anchor_alignment.csv'
        code = pd.read_csv(align).iloc[10].get('arm2_instrument_type')
        if code == code and code is not None:            # not NaN
            from vlgp.annotations.instruments import instrument_label
            want_instr = instrument_label(float(code))
    except Exception as e:                                # noqa: BLE001
        print(f'    (instrument reference unavailable: {e})')
    ok = s == 200 and set(arms) == {'arm1', 'arm2', 'arm3', 'arm4'} \
        and isinstance(arms['arm2']['scope_position_mm'], list) \
        and len(arms['arm2']['scope_position_mm']) == 3 \
        and (want_instr is None or arms['arm2']['instrument'] == want_instr)
    check('row status 4 arms + instruments', ok,
          f"arm2 instr={arms.get('arm2', {}).get('instrument')} (want {want_instr})")
    s, _, _ = get(base, f'/api/sessions/{session}/row/999999')
    check('row status out of range 404', s == 404)

    # ------------------------------------------------------------ cleanup
    if not args.keep:
        for p, blob in touched.items():
            if blob is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(blob)
        if had_manifest and manifest_backup is not None:
            manifest_path.write_text(manifest_backup, encoding='utf-8')
        else:
            manifest_path.unlink(missing_ok=True)
        print(f'\n(test artifacts restored: {len(touched)} annotations.json)')

    # -------------------------------------------------------------- summary
    n_pass = sum(1 for _, st, _ in _results if st == PASS)
    n_skip = sum(1 for _, st, _ in _results if st == SKIP)
    n_fail = len(_results) - n_pass - n_skip
    print(f'\n===== regression: {n_pass}/{len(_results) - n_skip} PASS'
          f' ({n_skip} skipped) =====')
    for name, st, detail in _results:
        if st == FAIL:
            print(f'  FAILED: {name} {detail}')
        elif st == SKIP:
            print(f'  SKIPPED: {name} ({detail})')
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
