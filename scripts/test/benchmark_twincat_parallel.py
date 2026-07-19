"""Benchmark parallel ADS connections to see if network bandwidth is the bottleneck."""
from __future__ import annotations

import threading
import time

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

DURATION = 5.0


def read_loop(name: str, symbol: str, duration: float, results: dict):
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    samples = 0
    start = time.perf_counter()
    deadline = start + duration
    while time.perf_counter() < deadline:
        plc.read_list_by_name([symbol])
        samples += 1
    total = time.perf_counter() - start
    plc.close()
    results[name] = (samples, total)


def main():
    # Single connection baseline.
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    symbol = 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame'
    samples = 0
    start = time.perf_counter()
    deadline = start + DURATION
    while time.perf_counter() < deadline:
        plc.read_list_by_name([symbol])
        samples += 1
    total = time.perf_counter() - start
    plc.close()
    print(f'Single connection, one symbol: {samples} samples, {samples/total:.1f} Hz')

    # Two parallel connections, each reading one symbol.
    results = {}
    t1 = threading.Thread(target=read_loop, args=('conn1', symbol, DURATION, results))
    t2 = threading.Thread(target=read_loop, args=('conn2', symbol, DURATION, results))
    start = time.perf_counter()
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    total = time.perf_counter() - start
    total_samples = sum(r[0] for r in results.values())
    print(f'Two parallel connections, same symbol: {total_samples} total samples in {total:.3f}s = {total_samples/total:.1f} Hz aggregate')
    for name, (samples, duration) in results.items():
        print(f'  {name}: {samples} samples, {samples/duration:.1f} Hz')


if __name__ == '__main__':
    raise SystemExit(main())
