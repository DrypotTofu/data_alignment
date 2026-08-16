"""Calibrate video frame timestamps onto the TwinCAT PLC time axis.

After a recording, PC and PLC crystal drift means that online timestamps
(which used ``clock_source: twincat_master``) can slowly diverge from the
exact PLC CycleCount axis. This script fits a line through the TwinCAT
samples and re-maps every video frame's raw PC arrival time onto that PLC
axis.

Usage:
    python scripts/tools/calibrate_temporal_drift.py \
        --twincat sessions/my_session/robot_poses/twincat_poses.csv \
        --video-metadata sessions/my_session/camera_wrist_left/metadata.csv \
        --output sessions/my_session/camera_wrist_left/metadata_calibrated.csv

The output CSV is a copy of the input with the ``timestamp_ns`` column
replaced by the drift-corrected PLC-axis timestamp.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from acquisition.clock_fit import fit_linear_timestamps


def load_twincat_samples(path: Path) -> list[dict[str, str]]:
    """Load TwinCAT CSV rows as dictionaries."""
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise ValueError(f'No rows in TwinCAT CSV: {path}')
    required = {'timestamp_ns', 'pc_arrival_ns', 'twincat_cycle_count'}
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(
            f'TwinCAT CSV missing required columns: {missing}. '
            'Ensure timestamp_mode=twincat and a recent source version.'
        )
    return rows


def fit_drift_model(
    twincat_rows: list[dict[str, str]],
    sample_interval: int = 100,
) -> tuple[float, float, dict[str, float]]:
    """Fit twincat_timestamp_ns = k * pc_arrival_ns + b.

    Returns (k, b, diagnostics).
    """
    xs = []
    ys = []
    for i, row in enumerate(twincat_rows):
        if i % sample_interval != 0:
            continue
        try:
            x = int(row['pc_arrival_ns'])
            y = int(row['timestamp_ns'])
        except (KeyError, ValueError):
            continue
        xs.append(x)
        ys.append(y)

    if len(xs) < 2:
        raise ValueError(
            f'Need at least 2 TwinCAT samples for drift fit, got {len(xs)}. '
            'Try a smaller --sample-interval.'
        )

    # Linear fit: y = k*x + b
    fit = fit_linear_timestamps(xs, ys)
    k = fit.slope
    b = fit.intercept

    diagnostics = {
        'samples_used': fit.samples,
        'duration_s': (xs[-1] - xs[0]) / 1e9,
        'k': fit.slope,
        'b_ns': fit.intercept,
        'residual_mean_ns': fit.residual_mean,
        'residual_std_ns': fit.residual_std,
        'residual_max_abs_ns': fit.residual_max_abs,
    }
    return k, b, diagnostics


def calibrate_video_metadata(
    video_path: Path,
    output_path: Path,
    k: float,
    b: float,
    timestamp_col: str = 'timestamp_ns',
    pc_arrival_col: str = 'pc_arrival_ns',
) -> int:
    """Write a calibrated copy of the video metadata CSV.

    The timestamp column is replaced by k * pc_arrival_ns + b.
    Returns the number of rows written.
    """
    with open(video_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if pc_arrival_col not in fieldnames:
        raise ValueError(
            f'Video metadata missing column {pc_arrival_col!r}. '
            'Old recordings do not store raw PC arrival time; '
            'use a source version that writes pc_arrival_ns.'
        )
    if timestamp_col not in fieldnames:
        raise ValueError(f'Video metadata missing timestamp column {timestamp_col!r}')

    calibrated_rows = []
    for row in rows:
        try:
            pc_arrival = int(row[pc_arrival_col])
        except (KeyError, ValueError):
            raise ValueError(f'Invalid {pc_arrival_col} value: {row.get(pc_arrival_col)!r}')
        calibrated_ts = int(round(k * pc_arrival + b))
        new_row = dict(row)
        new_row[timestamp_col] = calibrated_ts
        new_row['timestamp_ns_original'] = row[timestamp_col]
        calibrated_rows.append(new_row)

    out_fieldnames = fieldnames + ['timestamp_ns_original']
    # Ensure timestamp_ns is first if it was first before.
    if out_fieldnames[0] != timestamp_col and timestamp_col in out_fieldnames:
        out_fieldnames.remove(timestamp_col)
        out_fieldnames.insert(0, timestamp_col)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        writer.writerows(calibrated_rows)

    return len(calibrated_rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Calibrate video timestamps onto the TwinCAT PLC time axis.'
    )
    parser.add_argument(
        '--twincat', required=True, type=Path,
        help='Path to twincat_poses.csv',
    )
    parser.add_argument(
        '--video-metadata', required=True, type=Path,
        help='Path to camera metadata.csv',
    )
    parser.add_argument(
        '--output', required=True, type=Path,
        help='Path for the calibrated metadata CSV',
    )
    parser.add_argument(
        '--sample-interval', type=int, default=100,
        help='Use every N-th TwinCAT row for the drift fit (default: 100)',
    )
    parser.add_argument(
        '--timestamp-column', default='timestamp_ns',
        help='Name of the timestamp column in video metadata (default: timestamp_ns)',
    )
    parser.add_argument(
        '--pc-arrival-column', default='pc_arrival_ns',
        help='Name of the raw PC arrival column (default: pc_arrival_ns)',
    )
    args = parser.parse_args()

    if not args.twincat.exists():
        print(f'ERROR: TwinCAT CSV not found: {args.twincat}', file=sys.stderr)
        return 1
    if not args.video_metadata.exists():
        print(f'ERROR: Video metadata not found: {args.video_metadata}', file=sys.stderr)
        return 1

    twincat_rows = load_twincat_samples(args.twincat)
    k, b, diag = fit_drift_model(twincat_rows, sample_interval=args.sample_interval)

    print('Drift model: timestamp_ns = k * pc_arrival_ns + b')
    print(f'  k                 = {diag["k"]:.12f}')
    print(f'  b (ns)            = {diag["b_ns"]:.0f}')
    print(f'  samples used      = {diag["samples_used"]}')
    print(f'  fit duration (s)  = {diag["duration_s"]:.3f}')
    print(f'  residual std (ns) = {diag["residual_std_ns"]:.0f}')
    print(f'  residual max (ns) = {diag["residual_max_abs_ns"]:.0f}')

    # Frequency ratio interpretation.
    freq_ppm = (k - 1.0) * 1e6
    print(f'  implied PLC/PC frequency ratio = {freq_ppm:+.3f} ppm')

    n_written = calibrate_video_metadata(
        args.video_metadata,
        args.output,
        k,
        b,
        timestamp_col=args.timestamp_column,
        pc_arrival_col=args.pc_arrival_column,
    )
    print(f'Wrote {n_written} calibrated rows to {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
