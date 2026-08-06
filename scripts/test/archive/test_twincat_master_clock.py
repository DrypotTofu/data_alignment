"""Verify TwinCAT master clock offsets camera timestamps onto PLC axis."""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquisition.sources.twincat import TwinCATConfig, TwinCATSource
from acquisition.timestamp import ClockConfig, TimestampSource, UnifiedTimestamp


def main():
    # Shared clock under TwinCAT master domain.
    clock_cfg = ClockConfig(
        source=TimestampSource.TWINCAT_MASTER,
        offset_ns=0,
    )
    clock = UnifiedTimestamp(clock_cfg)

    twincat_cfg = TwinCATConfig(
        timestamp_mode='twincat',
        symbols={
            'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
            'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
        },
        symbol_shapes={
            'armframe_positions': (4, 3),
            'orientations': (4, 4),
        },
        twincat_master_sync_alpha=0.1,
    )

    q: queue.Queue = queue.Queue()
    twincat_source = TwinCATSource(config=twincat_cfg, clock=clock, output_queue=q)

    print('Starting TwinCAT source in master-clock mode...')
    twincat_source.start()
    time.sleep(2.0)

    # Simulate a camera source using the same clock.
    camera_samples = []
    for _ in range(10):
        camera_samples.append(clock.now_ns())
        time.sleep(0.05)

    twincat_source.stop(timeout=5.0)

    samples = []
    while not q.empty():
        samples.append(q.get())

    print(f'TwinCAT samples: {len(samples)}')
    print(f'Camera samples: {len(camera_samples)}')
    print(f'Final master offset_ns: {clock.config.offset_ns}')

    # Verify camera timestamps are close to TwinCAT timestamps.
    twincat_ts = [s.timestamp_ns for s in samples]
    twincat_latest = twincat_ts[-1]
    camera_latest = camera_samples[-1]
    print(f'Latest TwinCAT timestamp_ns: {twincat_latest}')
    print(f'Latest camera timestamp_ns:  {camera_latest}')
    print(f'Difference: {abs(camera_latest - twincat_latest) / 1e6:.3f} ms')

    # Also verify offset is stable (converged after ~2s).
    offsets = [s.metadata['twincat_cycle_count'] * s.metadata['twincat_cycle_time_ns']
               - s.metadata['pc_arrival_ns'] for s in samples]
    print(f'Offset range: {min(offsets)} .. {max(offsets)} ns')

    assert abs(camera_latest - twincat_latest) < 100_000_000, (
        'Camera and TwinCAT timestamps should share the same axis'
    )
    print('Master clock test passed.')


if __name__ == '__main__':
    main()
