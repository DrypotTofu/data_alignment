"""Unit test for calibrate_temporal_drift.py using synthetic data."""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.calibrate_temporal_drift import (
    calibrate_video_metadata,
    fit_drift_model,
    load_twincat_samples,
)


def write_twincat_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        'timestamp_ns', 'pc_arrival_ns', 'twincat_cycle_count',
        'twincat_cycle_time_ns', 'master_offset_ns',
    ]
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_video_metadata(path: Path, rows: list[dict]) -> None:
    fieldnames = ['index', 'timestamp_ns', 'pc_arrival_ns', 'filename']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    # Synthetic ground truth: twincat_timestamp = 1.000001 * pc_arrival + 1e12
    # with small noise. This simulates ~1 ppm frequency drift.
    k_true = 1.000001
    b_true = 1_000_000_000_000.0

    twincat_rows = []
    for i in range(1000):
        pc = 1_000_000_000_000 + i * 10_000_000  # 10 ms steps
        noise = (i % 7 - 3) * 1000  # small noise
        twincat = int(k_true * pc + b_true + noise)
        twincat_rows.append({
            'timestamp_ns': twincat,
            'pc_arrival_ns': pc,
            'twincat_cycle_count': i,
            'twincat_cycle_time_ns': 1_000_000,
            'master_offset_ns': twincat - pc,
        })

    video_rows = []
    for i in range(100):
        pc = 1_000_000_000_000 + i * 100_000_000  # 100 ms steps
        # Online timestamp has the same drift model but with larger noise.
        online_ts = int(k_true * pc + b_true + (i % 5 - 2) * 5000)
        video_rows.append({
            'index': i,
            'timestamp_ns': online_ts,
            'pc_arrival_ns': pc,
            'filename': f'frame_{i:04d}.png',
        })

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        twincat_path = tmp / 'twincat_poses.csv'
        video_path = tmp / 'metadata.csv'
        output_path = tmp / 'metadata_calibrated.csv'

        write_twincat_csv(twincat_path, twincat_rows)
        write_video_metadata(video_path, video_rows)

        rows = load_twincat_samples(twincat_path)
        k, b, diag = fit_drift_model(rows, sample_interval=10)

        print(f'Fitted k={k:.12f}, b={b:.0f}')
        print(f'True     k={k_true:.12f}, b={b_true:.0f}')
        print(f'Residual std: {diag["residual_std_ns"]:.0f} ns')

        assert abs(k - k_true) < 1e-7, 'k fit inaccurate'
        assert abs(b - b_true) < 10e6, 'b fit inaccurate'

        n = calibrate_video_metadata(video_path, output_path, k, b)
        print(f'Calibrated {n} video rows')

        with open(output_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            calibrated = list(reader)

        for row in calibrated:
            pc = int(row['pc_arrival_ns'])
            expected = int(k_true * pc + b_true)
            actual = int(row['timestamp_ns'])
            err = abs(actual - expected)
            assert err < 50_000, f'Calibration error too large: {err} ns'

        print('Drift calibration test passed.')


if __name__ == '__main__':
    main()
