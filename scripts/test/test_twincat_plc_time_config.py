"""Verify test_twincat_plc_time.yaml loads and produces twincat timestamps."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_acquisition import build_source_from_config


def main():
    config_path = Path(__file__).resolve().parents[1] / 'config' / 'test_twincat_plc_time.yaml'
    with open(config_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    twincat_cfg = cfg['sources'][0]
    source = build_source_from_config(twincat_cfg)

    print(f'Source config: {source.config}')
    assert source.config.timestamp_mode == 'twincat'

    source.start()
    time.sleep(1.5)
    source.stop(timeout=5.0)

    samples = source.drain(max_samples=500)
    print(f'Collected {len(samples)} samples')
    assert len(samples) > 0

    s = samples[0]
    assert s.metadata['timestamp_mode'] == 'twincat'
    assert 'twincat_cycle_count' in s.metadata
    assert 'twincat_cycle_time_ns' in s.metadata
    assert s.metadata['twincat_cycle_time_ns'] == 1_000_000
    print(f'First sample timestamp_ns={s.timestamp_ns}, cycle_count={s.metadata["twincat_cycle_count"]}')
    print('Config test passed.')


if __name__ == '__main__':
    main()
