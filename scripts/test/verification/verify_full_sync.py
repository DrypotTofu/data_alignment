"""Verify multi-modal alignment for a full sync session.

Loads camera metadata and TwinCAT pose CSVs, finds the nearest pose sample for
each camera frame, and writes a combined alignment report.

Reads the current session layout (T13): camera metadata in
``<session>/camera_arm_<N>/metadata.csv`` and per-arm robot poses in
``<session>/robot_pose/robot_pose_arm<N>/twincat_poses.csv`` (a legacy single
``robot_pose/twincat_poses.csv`` is used as a fallback when no per-arm CSV
exists). Each camera is compared against the pose CSV of the arm it belongs to
(endoscope = arm_2, wrist_left = arm_1, wrist_right = arm_3).

Usage:
    python verification/verify_full_sync.py --session sessions/<session_name>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from vlgp.acquisition.session_layout import find_per_arm_robot_pose_csvs, legacy_robot_pose_path


def load_session(session_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[int, pd.DataFrame]]:
    """Load the three camera metadata CSVs and the per-arm robot pose CSVs."""
    endo = pd.read_csv(session_dir / 'camera_arm_2' / 'metadata.csv')
    left = pd.read_csv(session_dir / 'camera_arm_1' / 'metadata.csv')
    right = pd.read_csv(session_dir / 'camera_arm_3' / 'metadata.csv')
    arms: dict[int, pd.DataFrame] = {}
    for arm_id, path in find_per_arm_robot_pose_csvs(session_dir).items():
        arms[arm_id] = pd.read_csv(path)
    if not arms:
        legacy = legacy_robot_pose_path(session_dir)
        if legacy is not None:
            arms = {1: pd.read_csv(legacy)}
    return endo, left, right, arms


def nearest_pose(cam_ts: np.ndarray, tw_ts: np.ndarray, tw: pd.DataFrame) -> pd.DataFrame:
    """For each camera timestamp, return the nearest TwinCAT pose row."""
    idx = np.argmin(np.abs(cam_ts[:, None] - tw_ts[None, :]), axis=1)
    aligned = tw.iloc[idx].copy()
    aligned['camera_timestamp_ns'] = cam_ts
    aligned['time_diff_ms'] = (cam_ts - tw_ts[idx]) / 1e6
    return aligned


def _pose_time(tw: pd.DataFrame) -> np.ndarray:
    """Comparison axis for robot poses.

    Camera metadata ``timestamp_ns`` is on the host (PC) clock; the robot
    ``timestamp_ns`` column is the PLC master axis (different epoch). Compare
    against the robot's ``timestamp_ns_pc`` (host domain, same clock as the
    cameras) so nearest-pose diffs are meaningful.
    """
    col = 'timestamp_ns_pc' if 'timestamp_ns_pc' in tw.columns else 'timestamp_ns'
    return tw[col].values


# Camera name -> arm id whose pose CSV anchors it (endoscope=arm2, wrists=arm1/3).
CAM_ARM = {'endoscope': 2, 'left': 1, 'right': 3}


def main():
    parser = argparse.ArgumentParser(description='Verify full sync alignment.')
    parser.add_argument('--session', type=str, required=True, help='Path to session directory')
    args = parser.parse_args()

    session_dir = Path(args.session)
    endo, left, right, arms = load_session(session_dir)
    if not arms:
        raise SystemExit(f'No robot pose CSV found under {session_dir / "robot_pose"}')

    print(f'Session: {session_dir}')
    for arm_id, df in sorted(arms.items()):
        ts = df['timestamp_ns'].values
        print(f'TwinCAT arm{arm_id} poses: {len(df)} samples, duration={(ts[-1]-ts[0])/1e9:.2f}s')
    print(f'Endoscope frames: {len(endo)}, duration={(endo["timestamp_ns"].iloc[-1]-endo["timestamp_ns"].iloc[0])/1e9:.2f}s')
    print(f'Left wrist frames: {len(left)}, duration={(left["timestamp_ns"].iloc[-1]-left["timestamp_ns"].iloc[0])/1e9:.2f}s')
    print(f'Right wrist frames: {len(right)}, duration={(right["timestamp_ns"].iloc[-1]-right["timestamp_ns"].iloc[0])/1e9:.2f}s')

    out_dir = session_dir / 'aligned'
    out_dir.mkdir(exist_ok=True)

    for name, df in [('endoscope', endo), ('left', left), ('right', right)]:
        arm_id = CAM_ARM[name] if CAM_ARM[name] in arms else next(iter(arms))
        tw = arms[arm_id]
        tw_ts = _pose_time(tw)
        aligned = nearest_pose(df['timestamp_ns'].values, tw_ts, tw)
        out_path = out_dir / f'{name}_nearest_pose.csv'
        aligned.to_csv(out_path, index=False)
        diffs = aligned['time_diff_ms'].values
        print(f'\n{name} (vs arm{arm_id}) -> nearest pose:')
        print(f'  mean diff = {diffs.mean():.2f} ms')
        print(f'  std  diff = {diffs.std():.2f} ms')
        print(f'  min  diff = {diffs.min():.2f} ms')
        print(f'  max  diff = {diffs.max():.2f} ms')
        print(f'  wrote {out_path}')

    # Summary JSON
    summary = {
        'session': str(session_dir),
        'robot_arms': {f'arm{k}': len(v) for k, v in sorted(arms.items())},
        'cameras': {},
    }
    for name, df in [('endoscope', endo), ('left', left), ('right', right)]:
        arm_id = CAM_ARM[name] if CAM_ARM[name] in arms else next(iter(arms))
        tw = arms[arm_id]
        tw_ts = _pose_time(tw)
        aligned = nearest_pose(df['timestamp_ns'].values, tw_ts, tw)
        summary['cameras'][name] = {
            'arm_id': arm_id,
            'frame_count': len(df),
            'duration_s': (df['timestamp_ns'].iloc[-1] - df['timestamp_ns'].iloc[0]) / 1e9,
            'nearest_pose_diff_ms': {
                'mean': float(aligned['time_diff_ms'].mean()),
                'std': float(aligned['time_diff_ms'].std()),
                'min': float(aligned['time_diff_ms'].min()),
                'max': float(aligned['time_diff_ms'].max()),
            }
        }

    with open(out_dir / 'summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f'\nWrote summary to {out_dir / "summary.json"}')


if __name__ == '__main__':
    raise SystemExit(main())
