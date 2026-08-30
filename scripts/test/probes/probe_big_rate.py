# -*- coding: utf-8 -*-
"""Sample the big-session preload rate (disk-bound case), then stop."""
import json
import time
import urllib.request

B = 'http://127.0.0.1:8766/api/sessions/test_20260828_160624'


def main() -> int:
    req = urllib.request.Request(B + '/preload', data=b'', method='POST')
    urllib.request.urlopen(req, timeout=30).read()
    time.sleep(20)
    with urllib.request.urlopen(B + '/preload/status', timeout=10) as r:
        st = json.loads(r.read())
    rate = st['current'] / 20.0
    print(f'after 20s: current={st["current"]}/{st["total"]} '
          f'rate={rate:.0f} rows/s')
    return 0


if __name__ == '__main__':
    main()
