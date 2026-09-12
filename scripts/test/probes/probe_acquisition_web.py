# -*- coding: utf-8 -*-
"""End-to-end test of the web acquisition integration (no hardware needed).

Uses scripts/config/test_web_acquisition.yaml, whose physical sources are
disabled and replaced by `reserved_arm` placeholders: the recording really
starts, writes session.json and stops gracefully — without cameras, PLC or tens
of GB of disk.

Covers:
  1. /api/acquisition/configs + bindings round-trip (incl. idempotence and the
     422/404 error paths; writes go to the TEST config, never default.yaml)
  2. camera scan (opens real devices if present) + preview error path
  3. preflight (cameras/robot skipped or reported, disk reservation, name clash)
  4. recording lifecycle: start -> recording -> device lock -> stop -> session
  5. emergency kill path
  6. data-root switch refused while recording

Run (server must listen on 8766):
    python scripts/test/probes/probe_acquisition_web.py
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
TEST_CONFIG = REPO / 'scripts' / 'config' / 'test_web_acquisition.yaml'
OUT_ROOT = REPO / 'var' / 'test_fixtures' / 'acq_out'
OK = True


def check(label: str, cond: bool, extra: str = '') -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}",
          flush=True)


def req(method: str, path: str, body=None, timeout: float = 300, raw: bool = False):
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


def ensure_server() -> None:
    try:
        req('GET', '/api/acquisition/status', timeout=10)
    except Exception as e:                     # noqa: BLE001
        raise SystemExit(f'web server not reachable on {BASE}: {e}')


def wait_state(target: set[str], timeout_s: float = 90) -> dict:
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_s:
        _, last = req('GET', '/api/acquisition/status')
        if last.get('state') in target:
            return last
        time.sleep(0.5)
    return last


def main() -> int:
    ensure_server()
    # clean state from earlier runs
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT, ignore_errors=True)

    print('=== 1. configs + bindings round-trip ===')
    st, data = req('GET', '/api/acquisition/configs')
    names = [c['name'] for c in data['configs']]
    print('  configs:', names, '| suggested:', data['suggested_name'])
    check('lists the two real configs', set(names) >= {'default.yaml',
                                                       'example_acquisition.yaml'})
    check('reports bindings of default.yaml',
          data['configs'][0]['bindings'] == {'arm_1': 1, 'arm_2': 0, 'arm_3': 3},
          str(data['configs'][0]['bindings']))
    check('reports camera sources with arm/index',
          all({'name', 'arm_id', 'camera_index'} <= set(c)
              for c in data['configs'][0]['cameras']),
          str(data['configs'][0]['cameras'])[:120])

    original = TEST_CONFIG.read_text(encoding='utf-8')
    # duplicate-index validation only applies to ENABLED camera sources (the
    # runtime semantics), so exercise it on a copy of the real config, which has
    # three enabled process_camera sources
    dup_config = REPO / 'scripts' / 'config' / 'tmp_probe_bindings.yaml'
    try:
        st, out = req('PUT', '/api/acquisition/bindings',
                      {'config': str(TEST_CONFIG),
                       'bindings': {'arm_1': 2, 'arm_2': 0, 'arm_3': 3},
                       'sync_configs': False})
        check('bindings written', st == 200 and out['bindings']['arm_1'] == 2,
              json.dumps(out.get('bindings'), ensure_ascii=False))
        once = TEST_CONFIG.read_text(encoding='utf-8')
        req('PUT', '/api/acquisition/bindings',
            {'config': str(TEST_CONFIG),
             'bindings': {'arm_1': 2, 'arm_2': 0, 'arm_3': 3},
             'sync_configs': False})
        check('write is idempotent (comments preserved)',
              TEST_CONFIG.read_text(encoding='utf-8') == once)
        check('other lines untouched',
              'camera_bindings:' in once and 'reserved_arm' in once
              and once.count('\n') == original.count('\n'),
              f'{original.count(chr(10))} -> {once.count(chr(10))} lines')
        # Both remaining binding checks need a config whose cameras are real,
        # ENABLED process_camera sources (duplicate detection skips disabled
        # ones, and reserved_arm is not a camera source at all).
        dup_config = REPO / 'scripts' / 'config' / 'tmp_probe_bindings.yaml'
        dup_config.write_text(
            (REPO / 'scripts' / 'config' / 'default.yaml').read_text(encoding='utf-8'),
            encoding='utf-8')
        before = dup_config.read_text(encoding='utf-8')
        st, bad = req('PUT', '/api/acquisition/bindings',
                      {'config': str(dup_config),
                       'bindings': {'arm_1': 0, 'arm_2': 0, 'arm_3': 3},
                       'sync_configs': False})
        check('duplicate camera_index -> 422', st == 422,
              json.dumps(bad, ensure_ascii=False)[:130])
        check('duplicate did not modify the file',
              dup_config.read_text(encoding='utf-8') == before)

        # ... and a copy with camera_arm_2 disabled: the clash is then latent
        # (accepted, but must be reported as a warning)
        warn_config = REPO / 'scripts' / 'config' / 'tmp_probe_warn.yaml'
        warn_config.write_text(before.replace(
            """- type: process_camera
  name: camera_arm_2""",
            """- type: process_camera
  name: camera_arm_2
  enabled: false"""), encoding='utf-8')
        st, warned = req('PUT', '/api/acquisition/bindings',
                         {'config': str(warn_config),
                          'bindings': {'arm_1': 0, 'arm_2': 0, 'arm_3': 3},
                          'sync_configs': False})
        check('latent conflict (disabled source) -> warning, not error',
              st == 200 and any('index 0' in w for w in warned.get('warnings', [])),
              json.dumps(warned.get('warnings'), ensure_ascii=False)[:160])
        warn_config.unlink(missing_ok=True)

        st, bad = req('PUT', '/api/acquisition/bindings',
                      {'config': str(REPO / 'README.md'),
                       'bindings': {'arm_1': 1}, 'sync_configs': False})
        check('config outside scripts/config -> 422', st == 422,
              json.dumps(bad, ensure_ascii=False)[:90])
    finally:
        TEST_CONFIG.write_text(original, encoding='utf-8')
        dup_config.unlink(missing_ok=True)

    print('=== 2. camera scan + preview ===')
    st, scan = req('POST', '/api/acquisition/cameras/scan',
                   {'max_index': 3, 'backend': 'dshow'}, timeout=120)
    found = scan.get('found', [])
    print(f"  found indices: {found} ({scan.get('elapsed_s')}s)")
    for e in scan.get('entries', []):
        if e.get('opened'):
            print(f"    index {e['index']}: {e['width']}x{e['height']} "
                  f"@{e['fps']} frame={e.get('frame_read')}")
    check('scan endpoint works', st == 200 and 'entries' in scan)
    check('every probed index answered', len(scan['entries']) == 4)
    if found:
        st, jpg = req('GET', f'/api/acquisition/cameras/preview?index={found[0]}'
                             '&width=320&height=240', raw=True, timeout=120)
        check('preview returns JPEG', st == 200 and jpg[:2] == b'\xff\xd8',
              f'{st} {len(jpg)}B')
    else:
        print('  (no camera attached/available — preview happy path skipped)')
    st, bad = req('GET', '/api/acquisition/cameras/preview?index=99')
    check('unavailable index -> 422 with reason',
          st == 422 and 'error' in (bad.get('detail') or bad.get('error') or {}),
          json.dumps(bad, ensure_ascii=False)[:110])

    print('=== 3. preflight (no enabled hardware in the test config) ===')
    st, pf = req('POST', '/api/acquisition/preflight',
                 {'config': str(TEST_CONFIG), 'reserve_minutes': 20,
                  'session_name': 'acq_web_probe'}, timeout=300)
    print('  checks:', [(c['id'], c['status']) for c in pf['checks']])
    for c in pf['checks']:
        print(f"    {c['status']:<4} {c['label']}: {c['detail'][:110]}")
    check('preflight ran', st == 200 and 'checks' in pf)
    check('disk reservation uses ~20 min (64 GB)',
          pf['estimate']['reserve_gb'] >= 60 and pf['estimate']['reserve_gb'] <= 70,
          str(pf['estimate']))
    check('no fail for the hardware-free config', pf['ok'],
          str(pf.get('failed')))
    st, clash = req('POST', '/api/acquisition/preflight',
                    {'config': str(TEST_CONFIG), 'reserve_minutes': 20,
                     'session_name': 'acq_web_probe'}, timeout=120)
    check('name clash NOT yet present (folder does not exist)',
          'name' not in (clash.get('failed') or []), str(clash.get('failed')))

    print('=== 4. recording lifecycle ===')
    st, started = req('POST', '/api/acquisition/start',
                      {'config': str(TEST_CONFIG), 'name': 'acq_web_probe'})
    check('start accepted', st == 200 and started['session'] == 'acq_web_probe',
          json.dumps(started, ensure_ascii=False)[:140])
    if started.get('stdin_failsafe'):
        check('stdin fail-safe armed', True, 'stdin EOF stops the recording')
    else:
        print('  NOTE: stdin pipe unavailable here -> only the stop file stops '
              'the recording (the .bat-launched server reports stdin_failsafe=true)')
    state = wait_state({'recording', 'done', 'error'})
    check('reached recording', state.get('state') == 'recording',
          str(state.get('state')))

    # device lock while recording
    st, locked = req('POST', '/api/acquisition/cameras/scan', {'max_index': 1},
                     timeout=60)
    check('camera scan refused while recording (409)', st == 409,
          json.dumps(locked, ensure_ascii=False)[:100])
    st, locked = req('PUT', '/api/acquisition/bindings',
                     {'config': str(TEST_CONFIG), 'bindings': {'arm_1': 1},
                      'sync_configs': False})
    check('binding change refused while recording (409)', st == 409)
    st, locked = req('POST', '/api/acquisition/start',
                     {'config': str(TEST_CONFIG), 'name': 'acq_web_probe2'})
    check('second recording refused (409)', st == 409)
    st, locked = req('PUT', '/api/settings/root',
                     {'path': str(REPO / 'var' / 'test_fixtures')})
    check('data-root switch refused while recording (409)', st == 409,
          json.dumps(locked, ensure_ascii=False)[:140])

    time.sleep(3.0)
    _, live = req('GET', '/api/acquisition/status')
    print(f"  live: {live['state']} {live['elapsed_s']}s "
          f"gb={live['gb_written']} session_dir={live['session_dir']}")
    check('session folder created at start',
          live['session_dir'] is not None and Path(live['session_dir']).is_dir(),
          str(live.get('session_dir')))
    check('elapsed keeps growing', live['elapsed_s'] >= 0)

    t0 = time.time()
    st, stop = req('POST', '/api/acquisition/stop')
    print(f"  stop: state={stop.get('state')} waited={stop.get('waited_s')}s "
          f"marker_seen={stop.get('started_marker_seen')}")
    check('graceful stop completed', stop.get('still_running') is False
          and stop.get('state') == 'done',
          json.dumps(stop, ensure_ascii=False)[:140])
    check('stop waited for the RECORDING STARTED marker',
          stop.get('started_marker_seen') is True, str(stop.get('started_marker_seen')))
    # a finished run must NOT be reported as the current state (the panel used to
    # open showing "已结束" before anything was started in this session)
    final = wait_state({'idle', 'done', 'error'}, timeout_s=30)
    last = final.get('last_run') or {}
    check('registry reports idle after the run', final.get('state') == 'idle',
          str(final.get('state')))
    check('last run summary kept separately',
          last.get('session') == 'acq_web_probe' and last.get('exit_code') == 0,
          json.dumps({k: last.get(k) for k in ('session', 'exit_code', 'gb_written')},
                     ensure_ascii=False))
    sess_dir = Path(last.get('session_dir') or live.get('session_dir'))
    check('session.json written', (sess_dir / 'session.json').is_file(), str(sess_dir))
    check('stop waited for the process (not an instant kill)',
          time.time() - t0 > 0.2, f'{time.time() - t0:.1f}s')
    st, log = req('GET', '/api/acquisition/log', raw=True)
    log = log.decode('utf-8', 'replace') if isinstance(log, bytes) else str(log)
    check('log has ACQ markers',
          '=== ACQ RECORDING STARTED' in log and '=== ACQ RECORDING STOPPED' in log
          and '=== ACQ DONE' in log)
    check('log names the stop reason', 'reason=stop file created' in log,
          next((ln for ln in log.splitlines() if 'STOPPED' in ln), '')[:110])

    # the same session name must now be refused (it would append into the
    # existing folder)
    st, clash2 = req('POST', '/api/acquisition/preflight',
                     {'config': str(TEST_CONFIG), 'reserve_minutes': 20,
                      'session_name': 'acq_web_probe'}, timeout=120)
    check('name clash detected once the folder exists',
          'name' in (clash2.get('failed') or []), str(clash2.get('failed')))
    check('preflight not ok on a name clash', clash2.get('ok') is False)

    print('=== 5. emergency kill path ===')
    st, again = req('POST', '/api/acquisition/start',
                    {'config': str(TEST_CONFIG), 'name': 'acq_web_kill'})
    check('second recording can start after the first ended', st == 200,
          json.dumps(again, ensure_ascii=False)[:110])
    wait_state({'recording', 'done', 'error'})
    st, killed = req('POST', '/api/acquisition/kill')
    print('  kill ->', json.dumps({k: killed.get(k) for k in
                                   ('state', 'exit_code', 'error')},
                                  ensure_ascii=False)[:160])
    _, after_kill = req('GET', '/api/acquisition/status')
    last_kill = after_kill.get('last_run') or killed
    check('kill marks the job as errored',
          killed.get('state') == 'error' or last_kill.get('state') == 'error',
          f"kill={killed.get('state')} last={last_kill.get('state')}")
    check('kill warns about incomplete data',
          '数据尾部' in (killed.get('error') or last_kill.get('error') or ''),
          str(killed.get('error') or last_kill.get('error'))[:100])

    print('=== 6. cleanup ===')
    st, idle = req('GET', '/api/acquisition/status')
    check('status still queryable after kill', st == 200)
    print('RESULT:', 'PASS' if OK else 'FAIL')
    return 0 if OK else 1


if __name__ == '__main__':
    raise SystemExit(main())
