"""Benchmark multi-connection TwinCAT reads in a single session-like loop."""
from __future__ import annotations

import queue
import threading
import time

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

DURATION = 5.0

# Two connections read the same pose subset to double the effective pose rate.
POSE_SYMBOLS = [
    'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
    'Sir_RunRunShaw_Database.EndEffector_Orientation',
]


def reader(name: str, symbols: list[str], q: queue.Queue, duration: float):
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    start = time.perf_counter()
    deadline = start + duration
    samples = 0
    while time.perf_counter() < deadline:
        t = time.perf_counter()
        plc.read_list_by_name(symbols)
        q.put((name, t))
        samples += 1
    total = time.perf_counter() - start
    plc.close()
    return samples, total


def main():
    q: queue.Queue = queue.Queue()

    # Single connection baseline.
    t0 = threading.Thread(target=reader, args=('single', POSE_SYMBOLS, q, DURATION))
    t0.start()
    t0.join()

    # Drain queue.
    single_samples = 0
    while not q.empty():
        q.get()
        single_samples += 1
    print(f'Single connection pose rate: {single_samples / DURATION:.1f} Hz')

    # Two parallel connections.
    threads = [
        threading.Thread(target=reader, args=('pose_conn1', POSE_SYMBOLS, q, DURATION)),
        threading.Thread(target=reader, args=('pose_conn2', POSE_SYMBOLS, q, DURATION)),
    ]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.perf_counter() - start
    multi_samples = q.qsize()
    print(f'Two parallel connections pose rate: {multi_samples / total:.1f} Hz aggregate')

    # Three parallel connections.
    while not q.empty():
        q.get()
    threads = [
        threading.Thread(target=reader, args=('pose_conn1', POSE_SYMBOLS, q, DURATION)),
        threading.Thread(target=reader, args=('pose_conn2', POSE_SYMBOLS, q, DURATION)),
        threading.Thread(target=reader, args=('pose_conn3', POSE_SYMBOLS, q, DURATION)),
    ]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.perf_counter() - start
    multi_samples = q.qsize()
    print(f'Three parallel connections pose rate: {multi_samples / total:.1f} Hz aggregate')

    # Four parallel connections.
    while not q.empty():
        q.get()
    threads = [
        threading.Thread(target=reader, args=('pose_conn1', POSE_SYMBOLS, q, DURATION)),
        threading.Thread(target=reader, args=('pose_conn2', POSE_SYMBOLS, q, DURATION)),
        threading.Thread(target=reader, args=('pose_conn3', POSE_SYMBOLS, q, DURATION)),
        threading.Thread(target=reader, args=('pose_conn4', POSE_SYMBOLS, q, DURATION)),
    ]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.perf_counter() - start
    multi_samples = q.qsize()
    print(f'Four parallel connections pose rate: {multi_samples / total:.1f} Hz aggregate')


if __name__ == '__main__':
    raise SystemExit(main())
