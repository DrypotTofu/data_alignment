# -*- coding: utf-8 -*-
"""End-to-end test of the data-root picker (T15 extension).

Covers: /api/settings, /api/fs/{drives,list,reveal}, switching the active
data root (incl. the refusal while a job runs), the per-root preview cache,
per-root pipeline logs, and the acquisition-output-dir write-back.

The probe builds a throw-away root under var/probe_root (one fake session with
a copied alignment CSV + one frame), backs up var/web_settings.json and the
acquisition configs, and restores everything at the end (the active root is
left on the repo sessions/ folder).

NOTE: section 7 rewrites the real ``base_dir`` of both acquisition configs, so
if this probe is KILLED mid-run the cleanup is skipped — check
``git diff scripts/config/`` and restore ``base_dir: ./sessions`` (or
``git checkout -- scripts/config/example_acquisition.yaml``) afterwards.

Run (server must be listening on 8766):
    python scripts/test/probes/probe_web_root_switch.py
"""
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8766'
REPO = Path(__file__).resolve().parents[3]
SESSIONS = REPO / 'sessions'
SRC_SESSION = SESSIONS / 'test_20260906_113038'
BIG_SESSION = 'test_20260907_162250'
TMP = REPO / 'var' / 'probe_root'
TMP_SESSION = 'probe_sess'
SETTINGS = REPO / 'var' / 'web_settings.json'
ACQ_CONFIGS = (REPO / 'scripts' / 'config' / 'default.yaml',
               REPO / 'scripts' / 'config' / 'example_acquisition.yaml')

OK = True


def check(label: str, cond: bool, extra: str = '') -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}",
          flush=True)


def req(method: str, path: str, body=None, timeout: float = 60,
        raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
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


def wait_job(job_id: str, timeout_s: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        _, st = req('GET', f'/api/pipeline/{job_id}/status')
        if st['status'] != 'running':
            return st
        time.sleep(0.5)
    raise TimeoutError(job_id)


def build_tmp_root() -> None:
    if TMP.exists():
        shutil.rmtree(TMP)
    sess = TMP / TMP_SESSION
    (sess / 'camera_anchor_alignment').mkdir(parents=True)
    shutil.copy2(SRC_SESSION / 'camera_anchor_alignment' / 'camera_anchor_alignment.csv',
                 sess / 'camera_anchor_alignment' / 'camera_anchor_alignment.csv')
    cam = sess / 'camera_arm_2'
    cam.mkdir()
    shutil.copy2(SRC_SESSION / 'camera_arm_2' / 'metadata.csv',
                 cam / 'metadata.csv')
    import csv
    with open(sess / 'camera_anchor_alignment' / 'camera_anchor_alignment.csv',
              encoding='utf-8') as f:
        row0 = next(csv.DictReader(f))
    fname = row0['anchor_filename']
    shutil.copy2(SRC_SESSION / 'camera_arm_2' / fname, cam / fname)
    print(f'  built {TMP} (session {TMP_SESSION}, frame {fname})')


def main() -> int:
    print('=== backup ===')
    set_backup = SETTINGS.read_bytes() if SETTINGS.exists() else None
    acq_backup = {p: p.read_bytes() for p in ACQ_CONFIGS if p.exists()}
    print(f'  settings backup: {"yes" if set_backup else "absent"}')
    build_tmp_root()
    try:
        print('=== 1. /api/settings (repo root) ===')
        _, s = req('GET', '/api/settings')
        print('  ', {k: s.get(k) for k in ('root', 'n_sessions', 'writable')})
        check('root is sessions/', Path(s['root']).name == 'sessions', s['root'])
        check('n_sessions == 3', s['n_sessions'] == 3, str(s['n_sessions']))
        check('cache_dir follows root',
              Path(s['cache_dir']).parent == Path(s['root']),
              s['cache_dir'])
        check('disk info present', bool(s.get('disk')),
              json.dumps(s.get('disk'), ensure_ascii=False))
        check('acquisition base_dir reported',
              bool(s['acquisition'].get('base_dir')),
              str(s['acquisition'].get('base_dir')))

        print('=== 2. /api/fs/drives ===')
        _, d = req('GET', '/api/fs/drives')
        for dr in d['drives']:
            print(f"   {dr['label']:>6}  free={(dr['disk'] or {}).get('free_gb')} GB"
                  f" / {(dr['disk'] or {}).get('total_gb')} GB")
        check('drives listed', len(d['drives']) >= 1)
        check('drives carry capacity',
              all(x['disk'] for x in d['drives']), str(len(d['drives'])))

        print('=== 3. /api/fs/list (sessions root) ===')
        _, lst = req('GET', f'/api/fs/list?path={urllib.parse.quote(str(SESSIONS))}')
        names = [e['name'] for e in lst['entries']]
        expected = sorted(p.name for p in SESSIONS.iterdir()
                          if p.is_dir() and not p.name.startswith('_'))
        print('   entries:', names, '| n_sessions:', lst['n_sessions'])
        check('sessions listed as entries', sorted(names) == expected, str(names))
        check('all entries flagged as sessions',
              all(e['is_session'] for e in lst['entries']), str(names))
        check('cache dir hidden from listing',
              '_web_preview' not in names and '_web_logs' not in names)
        check('n_sessions == 3', lst['n_sessions'] == 3)
        check('parent link present', bool(lst['parent']), str(lst['parent']))
        st, bad = req('GET', '/api/fs/list?path=' + urllib.parse.quote(str(TMP / 'nope')))
        check('missing dir -> 422', st == 422, f'{st} {bad}')

        print('=== 4. switch root -> temp ===')
        st, sw = req('PUT', '/api/settings/root', {'path': str(TMP)})
        check('switch ok', st == 200, json.dumps(sw, ensure_ascii=False)[:200])
        check('root is temp', Path(sw['root']) == TMP, sw.get('root', ''))
        check('previous reported', Path(sw['previous']).name == 'sessions',
              str(sw.get('previous')))
        check('n_sessions == 1', sw['n_sessions'] == 1, str(sw['n_sessions']))
        check('settings persisted', SETTINGS.exists()
              and json.loads(SETTINGS.read_text(encoding='utf-8'))['sessions_root']
              == str(TMP))
        _, sess = req('GET', '/api/sessions')
        check('session list switched',
              [x['name'] for x in sess['sessions']] == [TMP_SESSION],
              str([x['name'] for x in sess['sessions']]))
        check('sessions payload carries root', Path(sess['root']) == TMP)

        print('=== 5. frames + per-root cache dir ===')
        st, data = req('GET', f'/api/sessions/{TMP_SESSION}/frames/camera_arm_2/0'
                              '?max_width=640', raw=True)
        check('frame served', st == 200 and data[:2] == b'\xff\xd8',
              f'{st} {len(data)}B')
        cached = TMP / '_web_preview' / TMP_SESSION / 'camera_arm_2' / 'w640' / '0.jpg'
        check('cache follows active root', cached.exists(), str(cached))

        print('=== 6. pipeline log dir follows active root ===')
        _, job = req('POST', f'/api/sessions/{TMP_SESSION}/pipeline/verify')
        st = wait_job(job['id'])
        log = TMP / '_web_logs' / f"{job['id']}.log"
        print('   job:', st['status'], 'exit', st['exit_code'], '| log:', log)
        check('job log under <root>/_web_logs', log.exists(), str(log))
        check('log has content', log.exists() and log.stat().st_size > 0)

        print('=== 7. acquisition output dir write-back ===')
        st, acq = req('PUT', '/api/settings/acquisition',
                      {'base_dir': str(TMP), 'sync_configs': True})
        check('write-back ok', st == 200, json.dumps(acq, ensure_ascii=False)[:200])
        for cfg in acq['acquisition']['configs']:
            check(f"base_dir written: {Path(cfg['path']).name}",
                  cfg['base_dir'] == str(TMP), str(cfg['base_dir']))
        st, bad = req('PUT', '/api/settings/acquisition', {'base_dir': '  '})
        check('empty base_dir -> 422', st == 422, f'{st} {bad}')

        print('=== 8. switch back to repo root ===')
        st, back = req('PUT', '/api/settings/root', {'path': str(SESSIONS)})
        check('switched back', st == 200 and Path(back['root']) == SESSIONS)
        check('n_sessions == 3 again', back['n_sessions'] == 3,
              str(back['n_sessions']))

        print('=== 9. root switch refused while a job runs ===')
        # a running whole-session preload must block the switch (its paths were
        # resolved against the old root); make sure there is real work to do
        # first, otherwise a warm cache finishes the job before the PUT lands
        shutil.rmtree(SESSIONS / '_web_preview' / BIG_SESSION, ignore_errors=True)
        _, pr = req('POST', f'/api/sessions/{BIG_SESSION}/preload')
        _, pst = req('GET', f'/api/sessions/{BIG_SESSION}/preload/status')
        print('   preload:', pst)
        check('preload running with work left',
              pst['status'] == 'running' and pst['current'] < pst['total'],
              str(pst))
        st, busy = req('PUT', '/api/settings/root', {'path': str(TMP)})
        check('busy -> 409', st == 409, f'{st} {json.dumps(busy, ensure_ascii=False)[:160]}')
        check('busy names the preload session',
              any(BIG_SESSION in b for b in (busy.get('detail', {}).get('busy') or [])),
              json.dumps(busy, ensure_ascii=False)[:160])
        _, st_now = req('GET', '/api/settings')
        check('root unchanged after refusal', Path(st_now['root']) == SESSIONS,
              st_now['root'])
        # the preload keeps encoding this session's cache in the background
        # (that cache is valid and useful); the test server is killed after.

        print('=== 10. preview-cache scan (no deletion) ===')
        st, scan = req('GET', '/api/settings/cache')
        print(f"   total {scan['total_mb']} MB, stale {scan['stale_mb']} MB"
              f" ({scan['stale_count']})")
        print('   cache roots:', scan['cache_roots'])
        check('scan lists cache dirs', len(scan['dirs']) >= 3, str(len(scan['dirs'])))
        check('existing sessions not stale',
              all(not d['stale'] for d in scan['dirs'] if d['session'] in
                  {'test_20260906_113038', 'test_20260906_113307',
                   'test_20260907_162250'}))
        st, nc = req('POST', '/api/settings/cache/clean', {'confirm': False})
        check('clean without confirm -> 422', st == 422, f'{st} {nc}')
        check('nothing deleted', not (TMP / '_web_preview' / 'nonexistent').exists())
    finally:
        print('=== cleanup ===')
        try:
            req('PUT', '/api/settings/root', {'path': str(SESSIONS)})
        except Exception as e:      # noqa: BLE001
            print('  switch-back failed:', e)
        for p, blob in acq_backup.items():
            p.write_bytes(blob)
            print(f'  restored {p.name}')
        # prove the acquisition configs are byte-identical to the backup again
        for p, blob in acq_backup.items():
            same = p.read_bytes() == blob
            print(f'  [{"PASS" if same else "FAIL"}] {p.name} restored byte-identical')
            global OK
            OK = OK and same
        if set_backup is not None:
            SETTINGS.write_bytes(set_backup)
            print('  restored var/web_settings.json')
        elif SETTINGS.exists():
            SETTINGS.unlink()
            print('  removed probe-created var/web_settings.json')
        if TMP.exists():
            shutil.rmtree(TMP, ignore_errors=True)
            print(f'  removed {TMP}')
    print('RESULT:', 'PASS' if OK else 'FAIL')
    return 0 if OK else 1


if __name__ == '__main__':
    raise SystemExit(main())
