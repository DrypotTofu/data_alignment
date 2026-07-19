"""Benchmark TwinCAT sample rate with 1, 2, and 4 parallel ADS connections."""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquisition.sources.twincat import TwinCATConfig, TwinCATSource


def benchmark(num_connections: int, duration_s: float = 3.0) -> dict:
    config = TwinCATConfig(
        timestamp_mode='twincat',
        num_connections=num_connections,
        symbols={
            'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
            'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
        },
        symbol_shapes={
            'armframe_positions': (4, 3),
            'orientations': (4, 4),
        },
    )
    q: queue.Queue = queue.Queue()
    source = TwinCATSource(config=config, output_queue=q)

    source.start()
    time.sleep(duration_s)
    source.stop(timeout=10.0)

    samples = []
    while not q.empty():
        samples.append(q.get())

    if not samples:
        return {
            'num_connections': num_connections,
            'samples': 0,
            'rate_hz': 0.0,
            'unique_cycles': 0,
        }

    counts = [s.metadata['twincat_cycle_count'] for s in samples]
    unique_counts = len(set(counts))
    timestamps = [s.timestamp_ns for s in samples]
    duration_ns = max(timestamps) - min(timestamps)
    rate = len(samples) / (duration_ns / 1e9) if duration_ns > 0 else 0.0

    return {
        'num_connections': num_connections,
        'samples': len(samples),
        'rate_hz': rate,
        'unique_cycles': unique_counts,
        'duplicates': len(samples) - unique_counts,
    }


def main():
    print('Benchmarking TwinCAT with different numbers of ADS connections...')
    for n in [1, 2, 4]:
        result = benchmark(n, duration_s=3.0)
        print(
            f'{n} connection(s): '
            f'{result["samples"]:>5} samples, '
            f'{result["rate_hz"]:>7.1f} Hz, '
            f'{result["unique_cycles"]:>5} unique cycles, '
            f'{result["duplicates"]:>5} duplicates'
        )


if __name__ == '__main__':
    main()
