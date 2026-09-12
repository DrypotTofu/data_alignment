# -*- coding: utf-8 -*-
"""End-to-end test of session deletion + trash (no hardware needed).

Runs against the synthetic fixture root (var/test_fixtures/synth_3cam), so the
sessions it creates and deletes are throw-away:

  1. delete-preview reports size/content
  2. delete without confirm -> 422, nothing happens
  3. delete (trash) -> session gone from the list, present in /api/trash,
     preview cache dropped, restorable
  4. restore -> back in the list with its rows
  5. permanent delete -> gone and not in the trash
  6. guards: refused while a recording runs (409) and on an unknown session (404)
  7. _trash never appears as a session

Usage:  python scripts/test/probes/probe_session_delete.py [--base URL]
"""
from __future__ import annotations

import argparse
import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO / 'var' / 'test_fixtures' / 'synth_3cam'
FIXTURE_SESSION = 'synth_3cam_0001'
TEST_CONFIG = REPO / 'scripts' / 'config' / 'test_web_acquisition.yaml'
OK = True


def check(label: str, cond: bool, extra: str = '') -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}",
          flush=True)


def req(base: str, method: str, path: str, body=None, timeout: float = 600,
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


def sessions(base: str) -> list[dict]:
    _, body = req(base, 'GET', '/api/sessions')
    return body['sessions']


def clone_fixture(name: str) -> Path:
    """Copy the fixture session so the original stays intact."""
    src = FIXTURE_ROOT / FIXTURE_SESSION
    dst = FIXTURE_ROOT / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8766')
    args = ap.parse_args()
    base = args.base.rstrip('/')

    st, before = req(base, 'GET', '/api/settings')
    original = before['root']
    name = 'del_probe_1'
    try:
        st, _ = req(base, 'PUT', '/api/settings/root', {'path': str(FIXTURE_ROOT)})
        check('switched to the fixture root', st == 200, f'HTTP {st}')
        clone_fixture(name)

        print('=== 1. delete-preview ===')
        st, info = req(base, 'GET', f'/api/sessions/{name}/delete-preview')
        print(f"  size={info.get('mb')} MB files={info.get('files')} "
              f"rows={info.get('n_rows')} cameras={list((info.get('cameras') or {}))}")
        check('preview reports content', st == 200 and info['files'] > 0
              and info['n_rows'] > 0 and info['bytes'] > 0)
        check('preview reports the alignment state', info['has_alignment'] is True)

        print('=== 2. delete without confirm ===')
        st, bad = req(base, 'POST', f'/api/sessions/{name}/delete?mode=trash')
        check('refused without confirm (422)', st == 422,
              json.dumps(bad, ensure_ascii=False)[:110])
        check('session still present',
              any(s['name'] == name for s in sessions(base)))

        print('=== 3. delete -> trash ===')
        # give the session a preview cache so we can see it dropped
        st, jpg = req(base, 'GET',
                      f'/api/sessions/{name}/frames/camera_arm_2/0?max_width=64',
                      raw=True)
        check('frame served (cache will be written)', st == 200 and jpg[:2] == b'\xff\xd8',
              f'{st} {len(jpg)}B')
        cache_dir = FIXTURE_ROOT / '_web_preview' / name
        check('preview cache created first', cache_dir.is_dir(), str(cache_dir))
        st, out = req(base, 'POST', f'/api/sessions/{name}/delete?mode=trash&confirm=1')
        print(f"  mode={out.get('mode')} entry={out.get('trash_entry')} "
              f"cache_dirs_removed={out.get('cache_dirs_removed')}")
        check('delete accepted', st == 200 and out['mode'] == 'trash')
        check('moved into _trash', Path(out['trashed_to']).is_dir())
        check('gone from the session list',
              not any(s['name'] == name for s in sessions(base)))
        check('preview cache dropped', not cache_dir.exists())
        check('_trash is not listed as a session',
              not any(s['name'] == '_trash' for s in sessions(base)))
        st, trash = req(base, 'GET', '/api/trash')
        check('listed in the trash', any(e['entry'] == out['trash_entry']
                                        for e in trash['entries']),
              f"count={trash['count']} total={trash['total_gb']} GB")
        entry = next((e for e in trash['entries']
                      if e['entry'] == out['trash_entry']), {})
        check('trash entry reports its size',
              entry.get('files', 0) > 0 and entry.get('mb', 0) > 0,
              f"{entry.get('files')} files, {entry.get('mb')} MB")

        print('=== 4. restore ===')
        st, res = req(base, 'POST', '/api/trash/restore', {'entry': out['trash_entry']})
        check('restored', st == 200 and Path(res['restored']).is_dir(),
              json.dumps(res, ensure_ascii=False)[:110])
        back = [s for s in sessions(base) if s['name'] == name]
        check('back in the session list with its rows', back and back[0]['n_rows'] > 0,
              str(back[:1])[:110])
        st, trash2 = req(base, 'GET', '/api/trash')
        check('no longer in the trash', not any(e['entry'] == out['trash_entry']
                                               for e in trash2['entries']))
        # restoring twice must not clobber an existing folder
        st, dup = req(base, 'POST', '/api/trash/restore', {'entry': out['trash_entry']})
        check('restoring a missing entry -> 422', st == 422,
              json.dumps(dup, ensure_ascii=False)[:90])

        print('=== 5. guards ===')
        st, bad = req(base, 'POST', '/api/sessions/no_such_session/delete?confirm=1')
        check('unknown session -> 404', st == 404, f'HTTP {st}')
        st, bad = req(base, 'POST', f'/api/sessions/{name}/delete?mode=weird&confirm=1')
        check('unknown mode -> 422', st == 422, f'HTTP {st}')
        st, started = req(base, 'POST', '/api/acquisition/start',
                          {'config': str(TEST_CONFIG), 'name': 'del_guard_probe'})
        check('recording started for the guard test', st == 200,
              json.dumps(started, ensure_ascii=False)[:90])
        st, busy = req(base, 'POST', f'/api/sessions/{name}/delete?confirm=1')
        check('delete refused while recording (409)', st == 409,
              json.dumps(busy, ensure_ascii=False)[:110])
        req(base, 'POST', '/api/acquisition/stop')
        for _ in range(60):
            _, stt = req(base, 'GET', '/api/acquisition/status')
            if stt.get('state') in ('done', 'error', 'idle'):
                break
            import time
            time.sleep(1)

        print('=== 6. permanent delete + empty trash ===')
        st, out2 = req(base, 'POST', f'/api/sessions/{name}/delete?mode=permanent&confirm=1')
        print(f"  permanent delete -> HTTP {st} {json.dumps(out2, ensure_ascii=False)[:160]}")
        check('permanent delete accepted', st == 200 and out2.get('mode') == 'permanent')
        check('folder really gone', not Path(out2['path']).exists())
        check('not in the trash either',
              not any(e['entry'].startswith(name) for e in
                      req(base, 'GET', '/api/trash')[1]['entries']))
        # empty trash must require confirm
        st, nope = req(base, 'POST', '/api/trash/empty', {'confirm': False})
        check('empty trash without confirm -> 422', st == 422, f'HTTP {st}')
        st, emptied = req(base, 'POST', '/api/trash/empty', {'confirm': True})
        check('empty trash ran', st == 200 and 'freed_gb' in emptied,
              json.dumps(emptied, ensure_ascii=False)[:110])
        st, trash3 = req(base, 'GET', '/api/trash')
        check('trash now empty', trash3['count'] == 0, str(trash3['count']))
    finally:
        # leave the fixture root as found
        leftover = FIXTURE_ROOT / name
        if leftover.exists():
            shutil.rmtree(leftover)
        trash_dir = FIXTURE_ROOT / '_trash'
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
        req(base, 'PUT', '/api/settings/root', {'path': original})
        print(f'  (fixture root cleaned, active root restored: {original})')
    print('RESULT:', 'PASS' if OK else 'FAIL')
    return 0 if OK else 1


if __name__ == '__main__':
    raise SystemExit(main())
