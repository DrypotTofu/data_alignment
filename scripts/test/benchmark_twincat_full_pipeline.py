"""Benchmark TwinCAT through the full DataSource + Session writer pipeline."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyads

from acquisition.session import AcquisitionSession, SessionConfig
from acquisition.sources.twincat import TwinCATConfig, TwinCATSource


def main():
    cfg = SessionConfig(
        session_name='twincat_bench',
        base_dir='./sessions',
        clock_source='host_realtime',
        sources=[],
    )
    session = AcquisitionSession(cfg)

    source = TwinCATSource(config=TwinCATConfig(
        topic_name='robot/pose',
        cycle_period_ms=1.0,
        selected_arm=1,
        timestamp_mode='pc_arrival',
    ))
    session.register_source(source)
    session.start()

    duration = 5.0
    print(f'Running full pipeline for {duration}s...')
    time.sleep(duration)
    session.stop()

    summary = session.get_status()
    print(f"Samples written: {summary['samples_written']}")
    for name, src_summary in summary['sources'].items():
        print(f"Source: {name}, samples: {src_summary['sample_count']}")

    # Compute actual rate from CSV.
    import pandas as pd
    csv_path = Path(session.session_dir) / 'robot_pose' / 'twincat_poses.csv'
    df = pd.read_csv(csv_path)
    ts = df['timestamp_ns'].values
    rate = len(df) / ((ts[-1] - ts[0]) / 1e9)
    print(f'CSV rate: {len(df)} samples, {rate:.1f} Hz, mean_delta={(ts[-1]-ts[0])/(len(df)-1)/1e6:.3f}ms')


if __name__ == '__main__':
    raise SystemExit(main())
