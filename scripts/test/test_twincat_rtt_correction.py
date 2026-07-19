"""Verify TwinCATSource RTT correction and metadata fields."""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquisition.sources.twincat import TwinCATConfig, TwinCATSource


def main():
    config = TwinCATConfig(
        timestamp_mode='twincat',
        symbols={
            'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
            'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
        },
        symbol_shapes={
            'armframe_positions': (4, 3),
            'orientations': (4, 4),
        },
        use_rtt_correction=True,
    )

    q: queue.Queue = queue.Queue()
    source = TwinCATSource(config=config, output_queue=q)

    print('Starting TwinCAT source with use_rtt_correction=True...')
    source.start()
    time.sleep(2.0)
    source.stop(timeout=5.0)

    samples = []
    while not q.empty():
        samples.append(q.get())

    print(f'Collected {len(samples)} samples')
    assert len(samples) > 0

    s = samples[0]
    assert s.metadata['timestamp_mode'] == 'twincat'
    assert 'rtt_ns' in s.metadata
    assert 'pc_arrival_ns' in s.metadata
    assert 'pc_arrival_corrected_ns' in s.metadata

    rtts = [s.metadata['rtt_ns'] for s in samples]
    print(f'RTT min/mean/max: {min(rtts)} / {sum(rtts)/len(rtts):.0f} / {max(rtts)} ns')

    # Corrected arrival should be earlier than raw arrival.
    for s in samples:
        assert s.metadata['pc_arrival_corrected_ns'] <= s.metadata['pc_arrival_ns']

    print('RTT correction test passed.')


if __name__ == '__main__':
    main()
