"""Verify TwinCATSource output_dual_timestamps option."""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquisition.sources.twincat import TwinCATConfig, TwinCATSource


def run_mode(timestamp_mode: str) -> None:
    config = TwinCATConfig(
        timestamp_mode=timestamp_mode,
        output_dual_timestamps=True,
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

    print(f'\nTesting timestamp_mode={timestamp_mode} with dual timestamps...')
    source.start()
    time.sleep(1.5)
    source.stop(timeout=5.0)

    samples = []
    while not q.empty():
        samples.append(q.get())

    assert len(samples) > 0
    s = samples[0]
    assert 'timestamp_ns_pc' in s.metadata
    assert 'timestamp_ns_plc' in s.metadata
    assert 'twincat_cycle_count' in s.metadata

    print(f'  timestamp_ns (mode) = {s.timestamp_ns}')
    print(f'  timestamp_ns_pc     = {s.metadata["timestamp_ns_pc"]}')
    print(f'  timestamp_ns_plc    = {s.metadata["timestamp_ns_plc"]}')

    # PLC timestamp should be roughly offset behind PC timestamp.
    # (PLC starts counting from boot, PC from Unix epoch.)
    assert s.metadata['timestamp_ns_plc'] > 0
    assert s.metadata['timestamp_ns_pc'] > s.metadata['timestamp_ns_plc']


def main():
    run_mode('pc_arrival')
    run_mode('pc_cycle')
    run_mode('twincat')
    print('\nDual timestamp test passed for all modes.')


if __name__ == '__main__':
    main()
