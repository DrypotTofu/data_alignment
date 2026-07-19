"""Benchmark 3 parallel ADS connections reading disjoint variable groups."""
from __future__ import annotations

import threading
import time

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

DURATION = 5.0

GROUPS = {
    'pose': [
        'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
        'Sir_RunRunShaw_Database.EndEffector_Orientation',
    ],
    'scope': [
        'Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame',
    ],
    'joints': [
        'Sir_RunRunShaw_Database.Joint_Angles',
        'Sir_RunRunShaw_Database.Instrument_Type',
    ],
}


def read_loop(name: str, symbols: list[str], duration: float, results: dict):
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    samples = 0
    start = time.perf_counter()
    deadline = start + duration
    while time.perf_counter() < deadline:
        plc.read_list_by_name(symbols)
        samples += 1
    total = time.perf_counter() - start
    plc.close()
    results[name] = (samples, total)


def main():
    results = {}
    threads = []
    start = time.perf_counter()
    for name, symbols in GROUPS.items():
        t = threading.Thread(target=read_loop, args=(name, symbols, DURATION, results))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    total = time.perf_counter() - start
    total_samples = sum(r[0] for r in results.values())
    print(f'3 parallel connections: {total_samples} total samples in {total:.3f}s = {total_samples/total:.1f} Hz aggregate')
    for name, (samples, duration) in results.items():
        print(f'  {name}: {samples} samples, {samples/duration:.1f} Hz, mean={duration*1000/samples:.3f}ms')


if __name__ == '__main__':
    raise SystemExit(main())
