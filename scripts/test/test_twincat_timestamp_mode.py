"""Test TwinCATSource with timestamp_mode='twincat'."""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquisition.sources.twincat import TwinCATConfig, TwinCATSource


def main():
    config = TwinCATConfig(
        ams_net_id='169.254.226.99.1.1',
        ams_port=851,
        ip_address='192.168.22.22',
        cycle_period_ms=1.0,
        symbols={
            'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
            'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
        },
        symbol_shapes={
            'armframe_positions': (4, 3),
            'orientations': (4, 4),
        },
        selected_arm=1,
        timestamp_mode='twincat',
    )

    q: queue.Queue = queue.Queue()
    source = TwinCATSource(config=config, output_queue=q)

    print('Starting TwinCAT source with timestamp_mode=twincat...')
    source.start()
    time.sleep(3.0)
    source.stop(timeout=5.0)

    samples = []
    while not q.empty():
        samples.append(q.get())

    print(f'Collected {len(samples)} samples in 3 s')
    if not samples:
        print('No samples received!')
        return

    print('First sample:')
    s = samples[0]
    print(f'  timestamp_ns={s.timestamp_ns}')
    print(f'  data={s.data}')
    print(f'  metadata={s.metadata}')

    print('Last sample:')
    s = samples[-1]
    print(f'  timestamp_ns={s.timestamp_ns}')
    print(f'  metadata keys={list(s.metadata.keys())}')

    # Check timestamp monotonicity and increments.
    ts_diffs = [samples[i].timestamp_ns - samples[i - 1].timestamp_ns
                for i in range(1, len(samples))]
    print(f'Timestamp diff min/mean/max: {min(ts_diffs)} / {sum(ts_diffs)/len(ts_diffs):.0f} / {max(ts_diffs)} ns')

    # Check that twincat_cycle_count increments.
    counts = [s.metadata['twincat_cycle_count'] for s in samples]
    count_diffs = [counts[i] - counts[i - 1] for i in range(1, len(counts))]
    print(f'CycleCount diff min/mean/max: {min(count_diffs)} / {sum(count_diffs)/len(count_diffs):.2f} / {max(count_diffs)}')

    summary = source.get_summary()
    print(f'\nSource summary: {summary}')


if __name__ == '__main__':
    main()
