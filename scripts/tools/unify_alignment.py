"""Build a unified alignment CSV: one robot pose row with nearest frames from all cameras.

For every TwinCAT robot pose sample, find the temporally nearest frame from each
camera (arm_1, arm_2, arm_3) and write a single row containing the robot pose
plus the three camera frame references.

Usage:
    python scripts/tools/unify_alignment.py --session sessions/test_20260803_104724
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def nearest_frame_idx(video_pc: np.ndarray, target_pc: float) -> int:
    """Return index of video frame whose PC timestamp is nearest to target_pc."""
    idx = np.searchsorted(video_pc, target_pc)
    idx = np.clip(idx, 1, len(video_pc) - 1)
    left_diff = abs(target_pc - video_pc[idx - 1])
    right_diff = abs(target_pc - video_pc[idx])
    return idx - 1 if left_diff < right_diff else idx


def build_unified_alignment(session_dir: Path) -> pd.DataFrame:
    """Create one row per robot pose with nearest frames from all cameras."""
    twincat_path = session_dir / 'robot_pose' / 'twincat_poses.csv'
    if not twincat_path.exists():
        raise FileNotFoundError(f'Robot pose CSV not found: {twincat_path}')

    robot_df = pd.read_csv(twincat_path)

    # Prepare camera data and compute each camera's active time range in PC domain.
    cameras = {}
    for arm in ['camera_arm_1', 'camera_arm_2', 'camera_arm_3']:
        meta_path = session_dir / arm / 'metadata.csv'
        if not meta_path.exists():
            print(f'{arm}: metadata.csv not found, skipping')
            continue
        video_df = pd.read_csv(meta_path)
        # Multi-writer workers may interleave metadata rows; sort by timestamp.
        video_df = video_df.sort_values('timestamp_ns').reset_index(drop=True)
        video_pc = video_df['timestamp_ns'].values.astype(np.float64)
        cameras[arm] = {
            'df': video_df,
            'pc': video_pc,
            'start_pc': video_pc.min(),
            'stop_pc': video_pc.max(),
        }
        print(f'{arm}: active PC range [{video_pc.min():.0f}, {video_pc.max():.0f}] ns')

    if not cameras:
        raise RuntimeError('No camera metadata found')

    # Only keep robot poses that fall inside all cameras' active ranges.
    global_start_pc = max(cam['start_pc'] for cam in cameras.values())
    global_stop_pc = min(cam['stop_pc'] for cam in cameras.values())
    print(f'Common valid PC range: [{global_start_pc:.0f}, {global_stop_pc:.0f}] ns')

    pose_cols = [
        'position_x', 'position_y', 'position_z',
        'orientation_w', 'orientation_x', 'orientation_y', 'orientation_z',
    ]

    rows = []
    robot_pc = robot_df['timestamp_ns_pc'].values.astype(np.float64)
    robot_plc = robot_df['timestamp_ns'].values.astype(np.float64)

    for i, row in robot_df.iterrows():
        robot_ts_plc = robot_plc[i]
        robot_ts_pc = robot_pc[i]

        # Skip robot poses outside the common camera active window.
        in_common_range = global_start_pc <= robot_ts_pc <= global_stop_pc

        out_row = {
            'robot_idx': i,
            'robot_timestamp_ns_pc': int(robot_ts_pc),
            'robot_timestamp_ns_plc': int(robot_ts_plc),
            'all_cameras_active': in_common_range,
        }

        # Add robot pose data if available
        for col in pose_cols:
            if col in row:
                out_row[col] = row[col]

        # Find nearest frame from each camera (both robot and video timestamps
        # are already in the PC time domain, so no conversion is needed).
        all_valid = in_common_range
        for arm, cam in cameras.items():
            video_pc = cam['pc']
            video_df = cam['df']

            idx = nearest_frame_idx(video_pc, robot_ts_pc)

            frame_row = video_df.iloc[idx]
            error_ns = abs(video_pc[idx] - robot_ts_pc)
            error_ms = error_ns / 1e6

            out_row[f'{arm}_frame_idx'] = int(frame_row['index'])
            out_row[f'{arm}_filename'] = frame_row['filename']
            out_row[f'{arm}_timestamp_ns_pc'] = int(video_pc[idx])
            out_row[f'{arm}_alignment_error_ms'] = error_ms

        rows.append(out_row)

    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description='Build unified multi-camera alignment CSV')
    parser.add_argument('--session', type=Path, required=True, help='Session directory')
    parser.add_argument('--out-dir', type=Path, default=None, help='Output directory')
    args = parser.parse_args()

    session_dir = args.session
    out_dir = args.out_dir or session_dir / 'unified_alignment'
    out_dir.mkdir(parents=True, exist_ok=True)

    df = build_unified_alignment(session_dir)
    out_path = out_dir / 'unified_alignment.csv'
    df.to_csv(out_path, index=False)

    print(f'Unified alignment CSV saved: {out_path}')
    print(f'Total robot pose rows: {len(df)}')

    valid_df = df[df['all_cameras_active']].copy()
    print(f'Rows with all cameras active: {len(valid_df)} ({100 * len(valid_df) / len(df):.1f}%)')

    valid_path = out_dir / 'unified_alignment_valid.csv'
    valid_df.to_csv(valid_path, index=False)
    print(f'Valid-only CSV saved: {valid_path}')

    # Print error statistics per camera (only for rows inside common camera window)
    for arm in ['camera_arm_1', 'camera_arm_2', 'camera_arm_3']:
        err_col = f'{arm}_alignment_error_ms'
        if err_col in valid_df.columns:
            errors = valid_df[err_col].values
            print(f'\n=== {arm} nearest-frame error (common active window) ===')
            print(f'  Mean: {errors.mean():.3f} ms')
            print(f'  Std:  {errors.std():.3f} ms')
            print(f'  Max:  {errors.max():.3f} ms')
            print(f'  Median: {np.median(errors):.3f} ms')
            print(f'  95th percentile: {np.percentile(errors, 95):.3f} ms')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
