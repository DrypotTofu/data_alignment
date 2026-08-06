"""Sanity check that pc_arrival and pc_cycle modes still work."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquisition.sources.twincat import TwinCATConfig, TwinCATSource


def run_mode(mode: str) -> None:
    config = TwinCATConfig(
        timestamp_mode=mode,
        symbols={
            'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
            'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
        },
        symbol_shapes={
            'armframe_positions': (4, 3),
            'orientations': (4, 4),
        },
    )
    source = TwinCATSource(config=config)
    source.start()
    time.sleep(1.0)
    source.stop(timeout=5.0)
    samples = source.drain(max_samples=500)
    assert len(samples) > 0
    assert samples[0].metadata['timestamp_mode'] == mode
    assert 'pc_arrival_ns' in samples[0].metadata
    print(f'{mode}: {len(samples)} samples, first_ts={samples[0].timestamp_ns}')


def main():
    run_mode('pc_arrival')
    run_mode('pc_cycle')
    print('Legacy timestamp modes OK.')


if __name__ == '__main__':
    main()
