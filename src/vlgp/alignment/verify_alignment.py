"""Verify cross-modal alignment between video frames and TwinCAT poses.

Usage:
    python -m vlgp.alignment.verify_alignment --session sessions/test_20260803_104724
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


from vlgp.acquisition.session_layout import DEFAULT_CAMERA_NAMES, camera_metadata_path, resolve_robot_pose_csv
from vlgp.acquisition.clock_fit import LinearClockFit, fit_linear_timestamps


def fit_plc_pc_clock_model(twincat_df: pd.DataFrame) -> tuple[LinearClockFit, dict]:
    """Fit timestamp_ns (PLC) from timestamp_ns_pc (PC) without BLAS."""
    pc = twincat_df['timestamp_ns_pc'].values.astype(np.float64)
    plc = twincat_df['timestamp_ns'].values.astype(np.float64)
    fit = fit_linear_timestamps(pc, plc)

    residuals = np.asarray(fit.residuals, dtype=np.float64)

    diag = {
        'k': fit.slope,
        'b_ns': fit.intercept,
        'samples': len(pc),
        'duration_s': (pc[-1] - pc[0]) / 1e9,
        'residual_std_ns': fit.residual_std,
        'residual_max_abs_ns': fit.residual_max_abs,
        'freq_ppm': (fit.slope - 1.0) * 1e6,
    }
    return fit, diag


def align_video_to_twincat(
    video_metadata_path: Path,
    twincat_data: Path | pd.DataFrame,
    clock_fit: LinearClockFit,
    output_path: Path,
):
    """Map video frame timestamps to PLC axis and find nearest TwinCAT pose."""
    video_df = pd.read_csv(video_metadata_path)
    # Multi-writer workers may interleave metadata rows; sort by timestamp.
    video_df = video_df.sort_values('timestamp_ns').reset_index(drop=True)
    twincat_df = (
        pd.read_csv(twincat_data)
        if isinstance(twincat_data, Path)
        else twincat_data.copy()
    )

    video_pc = video_df['timestamp_ns'].values.astype(np.float64)
    video_plc = np.asarray([clock_fit.predict(x) for x in video_pc], dtype=np.float64)

    twincat_plc = twincat_df['timestamp_ns'].values.astype(np.float64)

    # Nearest neighbor indices.
    nearest_idx = np.searchsorted(twincat_plc, video_plc)
    nearest_idx = np.clip(nearest_idx, 1, len(twincat_plc) - 1)
    left_diff = np.abs(video_plc - twincat_plc[nearest_idx - 1])
    right_diff = np.abs(video_plc - twincat_plc[nearest_idx])
    use_left = left_diff < right_diff
    nearest_idx = np.where(use_left, nearest_idx - 1, nearest_idx)

    alignment_error_ms = np.abs(video_plc - twincat_plc[nearest_idx]) / 1e6
    valid_mask = (video_plc >= twincat_plc[0]) & (video_plc <= twincat_plc[-1])

    pose_cols = [
        'position_x', 'position_y', 'position_z',
        'orientation_w', 'orientation_x', 'orientation_y', 'orientation_z',
    ]

    aligned_rows = []
    for i, row in video_df.iterrows():
        idx = nearest_idx[i]
        pose_row = twincat_df.iloc[idx]
        aligned_rows.append({
            'frame_idx': int(row['index']),
            'video_timestamp_ns_pc': int(row['timestamp_ns']),
            'video_timestamp_ns_plc': int(round(video_plc[i])),
            'twincat_timestamp_ns_plc': int(pose_row['timestamp_ns']),
            'alignment_error_ms': alignment_error_ms[i],
            'twincat_idx': int(idx),
            **{col: pose_row[col] for col in pose_cols},
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(aligned_rows).to_csv(output_path, index=False)
    return alignment_error_ms, valid_mask


def _resolve_twincat_path(session_dir: Path, arm_id: int) -> Path | None:
    """Find the TwinCAT CSV for a given arm.

    Prefers the per-arm output from multi-arm mode
    (``robot_pose/robot_pose_arm<N>/twincat_poses.csv``), then falls back to
    the legacy single ``robot_pose/twincat_poses.csv``.
    """
    return resolve_robot_pose_csv(session_dir, arm_id)


def main():
    parser = argparse.ArgumentParser(description='Verify video/TwinCAT alignment')
    parser.add_argument('--session', type=Path, required=True,
                        help='Path to session directory')
    args = parser.parse_args()

    session_dir = args.session

    for arm in DEFAULT_CAMERA_NAMES:
        video_path = camera_metadata_path(session_dir, arm)
        output_path = session_dir / arm / 'aligned_twincat.csv'
        if not video_path.exists():
            print(f'{arm}: metadata.csv not found, skipping')
            continue

        # Infer arm_id from directory name, e.g. camera_arm_1 -> 1.
        arm_id = int(arm.split('_')[-1])
        twincat_path = _resolve_twincat_path(session_dir, arm_id)
        if twincat_path is None:
            print(f'{arm}: TwinCAT pose CSV not found, skipping')
            continue

        # If using the legacy single CSV that may contain multiple arms,
        # filter to the arm of interest for both drift fitting and alignment.
        df_twincat = pd.read_csv(twincat_path)
        if 'arm' in df_twincat.columns and len(df_twincat['arm'].unique()) > 1:
            df_twincat = df_twincat[df_twincat['arm'] == arm_id].copy()
            if df_twincat.empty:
                print(f'{arm}: no poses for arm {arm_id} in legacy CSV, skipping')
                continue

        clock_fit, diag = fit_plc_pc_clock_model(df_twincat)
        print('=== Fitting PLC-PC drift model ===')
        print(f"  k                 = {diag['k']:.12f}")
        print(f"  b (ns)            = {diag['b_ns']:.0f}")
        print(f"  samples           = {diag['samples']}")
        print(f"  duration (s)      = {diag['duration_s']:.3f}")
        print(f"  residual std (ns) = {diag['residual_std_ns']:.0f}")
        print(f"  residual max (ns) = {diag['residual_max_abs_ns']:.0f}")
        print(f"  freq ppm          = {diag['freq_ppm']:+.3f}")
        print()

        err, valid_mask = align_video_to_twincat(video_path, df_twincat, clock_fit, output_path)
        valid_err = err[valid_mask]
        print(f'=== {arm} ===')
        print(f'  TwinCAT source: {twincat_path}')
        print(f'  Frames: {len(err)}')
        print(f'  Valid frames (inside TwinCAT range): {len(valid_err)}')
        print(f'  Alignment error ms: mean={valid_err.mean():.3f}, std={valid_err.std():.3f}, max={valid_err.max():.3f}')
        print(f'  Aligned output: {output_path}')
        print()


if __name__ == '__main__':
    main()
