# -*- coding: utf-8 -*-
"""End-to-end test of the web pipeline + meta endpoints.

Pick the session from the server (the smallest one with an alignment table) —
the suite must not depend on a session that happened to exist on one machine.
"""
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:8766'


def _pick_session() -> str:
    with urllib.request.urlopen(BASE + '/api/sessions', timeout=60) as r:
        sessions = [s for s in json.loads(r.read())['sessions'] if s['n_rows']]
    if not sessions:
        raise SystemExit('no aligned session on the server')
    sessions.sort(key=lambda s: s['n_rows'])
    return sessions[0]['name']


SESS = _pick_session()


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={'Content-Type': 'application/json'},
                               method=method)
    with urllib.request.urlopen(r, timeout=600) as resp:
        raw = resp.read()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw


def wait_job(job_id, timeout_s=180):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        st, body = req('GET', f'/api/pipeline/{job_id}/status')
        if body != last:
            print(f'  [{job_id}] {body}')
            last = body
        if body['status'] != 'running':
            return body
        time.sleep(1.0)
    raise TimeoutError(job_id)


def main() -> int:
    # 1. verify
    print('=== pipeline/verify ===')
    _, job = req('POST', f'/api/sessions/{SESS}/pipeline/verify')
    print('started:', job)
    st = wait_job(job['id'])
    assert st['status'] == 'done', st
    _, log = req('GET', f'/api/pipeline/{job["id"]}/log')
    print(f'log tail ({len(log)} chars):')
    print(log[-500:])

    # 2. build (no-copy to keep the test cheap)
    print('=== pipeline/build (no_copy) ===')
    _, job = req('POST', f'/api/sessions/{SESS}/pipeline/build?no_copy=1')
    print('started:', job)
    st = wait_job(job['id'], timeout_s=300)
    print('final:', st)
    _, log = req('GET', f'/api/pipeline/{job["id"]}/log')
    print(f'log tail ({len(log)} chars):')
    print(log[-600:])

    # 3. meta round-trip
    print('=== session meta ===')
    _, meta = req('GET', f'/api/sessions/{SESS}/meta')
    print('current keys:', list(meta.keys()))
    meta['task_instruction'] = 'grasp the needle (web test)'
    meta['task_type'] = 'needle_grasping'
    s, saved = req('PUT', f'/api/sessions/{SESS}/meta', meta)
    print('PUT status', s, 'task_instruction=', saved['saved']['task_instruction'])
    s, bad = req('PUT', f'/api/sessions/{SESS}/meta',
                 {'task_type': 'bogus_type'})
    print('PUT invalid task_type status', s, '->', bad)
    return 0


if __name__ == '__main__':
    main()
