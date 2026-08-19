"""Verify multi-modal alignment for a full sync session.

Loads camera metadata and TwinCAT pose CSV, finds the nearest pose sample for
each camera frame, and writes a combined alignment report.

Usage:
    python test/verify_full_sync.py --session sessions/full_sync_test_*
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_session(session_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cameras_dir = session_dir / 'all_cameras'
    endo = pd.read_csv(cameras_dir / 'camera_endoscope_image_raw' / 'metadata.csv')
    left = pd.read_csv(cameras_dir / 'camera_wrist_left_image_raw' / 'metadata.csv')
    right = pd.read_csv(cameras_dir / 'camera_wrist_right_image_raw' / 'metadata.csv')
    tw = pd.read_csv(session_dir / 'robot_pose' / 'twincat_poses.csv')
    return endo, left, right, tw


def nearest_pose(cam_ts: np.ndarray, tw_ts: np.ndarray, tw: pd.DataFrame) -> pd.DataFrame:
    """For each camera timestamp, return the nearest TwinCAT pose row."""
    idx = np.argmin(np.abs(cam_ts[:, None] - tw_ts[None, :]), axis=1)
    aligned = tw.iloc[idx].copy()
    aligned['camera_timestamp_ns'] = cam_ts
    aligned['time_diff_ms'] = (cam_ts - tw_ts[idx]) / 1e6
    return aligned


def main():
    parser = argparse.ArgumentParser(description='Verify full sync alignment.')
    parser.add_argument('--session', type=str, required=True, help='Path to session directory')
    args = parser.parse_args()

    session_dir = Path(args.session)
    endo, left, right, tw = load_session(session_dir)
    tw_ts = tw['timestamp_ns'].values

    print(f'Session: {session_dir}')
    print(f'TwinCAT poses: {len(tw)} samples, duration={(tw_ts[-1]-tw_ts[0])/1e9:.2f}s')
    print(f'Endoscope frames: {len(endo)}, duration={(endo["timestamp_ns"].iloc[-1]-endo["timestamp_ns"].iloc[0])/1e9:.2f}s')
    print(f'Left wrist frames: {len(left)}, duration={(left["timestamp_ns"].iloc[-1]-left["timestamp_ns"].iloc[0])/1e9:.2f}s')
    print(f'Right wrist frames: {len(right)}, duration={(right["timestamp_ns"].iloc[-1]-right["timestamp_ns"].iloc[0])/1e9:.2f}s')

    out_dir = session_dir / 'aligned'
    out_dir.mkdir(exist_ok=True)

    for name, df in [('endoscope', endo), ('left', left), ('right', right)]:
        aligned = nearest_pose(df['timestamp_ns'].values, tw_ts, tw)
        out_path = out_dir / f'{name}_nearest_pose.csv'
        aligned.to_csv(out_path, index=False)
        diffs = aligned['time_diff_ms'].values
        print(f'\n{name} -> nearest pose:')
        print(f'  mean diff = {diffs.mean():.2f} ms')
        print(f'  std  diff = {diffs.std():.2f} ms')
        print(f'  min  diff = {diffs.min():.2f} ms')
        print(f'  max  diff = {diffs.max():.2f} ms')
        print(f'  wrote {out_path}')

    # Summary JSON
    summary = {
        'session': str(session_dir),
        'twincat_count': len(tw),
        'twincat_duration_s': (tw_ts[-1] - tw_ts[0]) / 1e9,
        'cameras': {},
    }
    for name, df in [('endoscope', endo), ('left', left), ('right', right)]:
        aligned = nearest_pose(df['timestamp_ns'].values, tw_ts, tw)
        summary['cameras'][name] = {
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
