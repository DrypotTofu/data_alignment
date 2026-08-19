"""Benchmark TwinCAT ADS read performance in isolation.

This script bypasses the acquisition framework and directly measures:
  - ADS read_list_by_name round-trip time
  - Loop overhead with different sleep strategies
  - Effective sample rate for 1 kHz target

Usage:
    python test/benchmark_twincat.py --duration 5
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Any

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

SYMBOLS = {
    'joint_angles': 'Sir_RunRunShaw_Database.Joint_Angles',
    'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
    'scopeframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame',
    'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
    'instrument_types': 'Sir_RunRunShaw_Database.Instrument_Type',
}


@dataclass
class BenchmarkResult:
    name: str
    samples: int
    duration_s: float
    mean_period_ms: float
    min_period_ms: float
    max_period_ms: float
    read_times_ms: list[float]


def reshape(flat: list[Any], shape: tuple[int, ...]) -> list[Any]:
    if len(shape) == 1:
        return list(flat)
    if len(shape) == 2:
        rows, cols = shape
        return [list(flat[i * cols:(i + 1) * cols]) for i in range(rows)]
    raise NotImplementedError


def benchmark_read_list(plc: pyads.Connection, duration_s: float) -> BenchmarkResult:
    """Measure only the ADS read_list_by_name round-trip time."""
    names = list(SYMBOLS.values())
    periods_ms = []
    samples = 0
    start = time.perf_counter()
    deadline = start + duration_s

    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        raw = plc.read_list_by_name(names)
        t1 = time.perf_counter()
        periods_ms.append((t1 - t0) * 1000)
        samples += 1

    total_s = time.perf_counter() - start
    return BenchmarkResult(
        name='ADS read_list_by_name only',
        samples=samples,
        duration_s=total_s,
        mean_period_ms=sum(periods_ms) / len(periods_ms),
        min_period_ms=min(periods_ms),
        max_period_ms=max(periods_ms),
        read_times_ms=periods_ms,
    )


def benchmark_loop_no_sleep(plc: pyads.Connection, duration_s: float) -> BenchmarkResult:
    """Measure full read + reshape + timestamp loop, no sleep."""
    names = list(SYMBOLS.values())
    shapes = {
        'joint_angles': (4, 12),
        'armframe_positions': (4, 3),
        'scopeframe_positions': (4, 3),
        'orientations': (4, 4),
        'instrument_types': (4,),
    }
    periods_ms = []
    samples = 0
    start = time.perf_counter()
    deadline = start + duration_s

    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        raw = plc.read_list_by_name(names)
        for key, ads_name in SYMBOLS.items():
            _ = reshape(raw[ads_name], shapes[key])
        _ = time.time_ns()
        t1 = time.perf_counter()
        periods_ms.append((t1 - t0) * 1000)
        samples += 1

    total_s = time.perf_counter() - start
    return BenchmarkResult(
        name='read + reshape + timestamp, no sleep',
        samples=samples,
        duration_s=total_s,
        mean_period_ms=sum(periods_ms) / len(periods_ms),
        min_period_ms=min(periods_ms),
        max_period_ms=max(periods_ms),
        read_times_ms=periods_ms,
    )


def benchmark_loop_with_sleep(plc: pyads.Connection, duration_s: float, period_ms: float) -> BenchmarkResult:
    """Measure loop with sleep-to-next-cycle, matching TwinCATSource behavior."""
    names = list(SYMBOLS.values())
    shapes = {
        'joint_angles': (4, 12),
        'armframe_positions': (4, 3),
        'scopeframe_positions': (4, 3),
        'orientations': (4, 4),
        'instrument_types': (4,),
    }
    periods_ms = []
    samples = 0
    start = time.perf_counter()
    deadline = start + duration_s
    next_cycle = start

    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        raw = plc.read_list_by_name(names)
        for key, ads_name in SYMBOLS.items():
            _ = reshape(raw[ads_name], shapes[key])
        _ = time.time_ns()
        samples += 1
        t1 = time.perf_counter()
        periods_ms.append((t1 - t0) * 1000)

        next_cycle += period_ms / 1000.0
        sleep_s = next_cycle - time.perf_counter()
        if sleep_s > 0:
            time.sleep(sleep_s)

    total_s = time.perf_counter() - start
    return BenchmarkResult(
        name=f'read + reshape + timestamp + {period_ms}ms sleep',
        samples=samples,
        duration_s=total_s,
        mean_period_ms=sum(periods_ms) / len(periods_ms),
        min_period_ms=min(periods_ms),
        max_period_ms=max(periods_ms),
        read_times_ms=periods_ms,
    )


def print_result(r: BenchmarkResult):
    print(f"\n[{r.name}]")
    print(f"  samples: {r.samples}")
    print(f"  duration: {r.duration_s:.3f}s")
    print(f"  effective rate: {r.samples / r.duration_s:.1f} Hz")
    print(f"  read/loop time: mean={r.mean_period_ms:.3f}ms, min={r.min_period_ms:.3f}ms, max={r.max_period_ms:.3f}ms")
    sorted_times = sorted(r.read_times_ms)
    p99 = sorted_times[int(len(sorted_times) * 0.99)]
    p95 = sorted_times[int(len(sorted_times) * 0.95)]
    print(f"  p95={p95:.3f}ms, p99={p99:.3f}ms")


def main():
    parser = argparse.ArgumentParser(description='Benchmark TwinCAT ADS reads')
    parser.add_argument('--duration', type=float, default=5.0, help='Duration per benchmark in seconds')
    args = parser.parse_args()

    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    print(f'Connected to {AMS_NET_ID}:{AMS_PORT} via {IP_ADDRESS}')
    print(f'PLC state: {plc.read_state()}')

    try:
        r1 = benchmark_read_list(plc, args.duration)
        print_result(r1)

        r2 = benchmark_loop_no_sleep(plc, args.duration)
        print_result(r2)

        r3 = benchmark_loop_with_sleep(plc, args.duration, 1.0)
        print_result(r3)
    finally:
        plc.close()
        print('\nConnection closed.')


if __name__ == '__main__':
    raise SystemExit(main())
