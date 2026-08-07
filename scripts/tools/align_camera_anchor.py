"""Camera-anchored alignment with pose interpolation (SRT-H style).

Unlike `unify_alignment.py` (robot-anchored nearest-frame), this tool anchors on
the frames of a chosen camera (default ``camera_arm_2``, 30 fps) so every output
row is one synchronised multi-view sample at one exact instant — the sample unit
a SRT-H-style low-level policy consumes.

Pipeline for each anchor frame with PLC-axis time ``t``:
  1. Map camera PC timestamps to the PLC time axis via a linear drift model
     fitted from the TwinCAT dual timestamps (``timestamp_ns`` vs
     ``timestamp_ns_pc``).
  2. Find the temporally nearest frame from every other camera and record the
     alignment error.
  3. Interpolate every robot arm's pose to ``t``: LERP for positions and
     scalar signals, SLERP for orientation quaternions, nearest sample for
     discrete states (instrument type, energy status).

Supports both layouts:
  - multi-arm:  robot_pose/robot_pose_arm<N>/twincat_poses.csv
  - legacy:     robot_pose/twincat_poses.csv (filtered by the ``arm`` column)

Usage:
    python scripts/tools/align_camera_anchor.py --session sessions/test_20260803_104724
    python scripts/tools/align_camera_anchor.py --session sessions/xxx --anchor camera_arm_2 \
        --cameras camera_arm_1 camera_arm_3 --include-joints
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

POSE_COLS = ['position_x', 'position_y', 'position_z']
ORIENT_COLS = ['orientation_w', 'orientation_x', 'orientation_y', 'orientation_z']
# Continuous scalar signals interpolated linearly when present.
LERP_COLS = ['gripper_angle', 'gripper_signal']
# Discrete states taken from the nearest sample when present.
NEAREST_COLS = ['instrument_type', 'energy_instrument_status']


def fit_plc_pc_model(robot_df: pd.DataFrame) -> tuple[float, float, dict]:
    """Fit timestamp_ns (PLC) = k * timestamp_ns_pc (PC) + b."""
    pc = robot_df['timestamp_ns_pc'].values.astype(np.float64)
    plc = robot_df['timestamp_ns'].values.astype(np.float64)
    k, b = np.polyfit(pc, plc, 1)
    residuals = plc - (k * pc + b)
    diag = {
        'k': float(k),
        'b_ns': float(b),
        'samples': len(pc),
        'duration_s': (pc[-1] - pc[0]) / 1e9,
        'residual_std_ms': float(np.std(residuals) / 1e6),
        'residual_max_abs_ms': float(np.max(np.abs(residuals)) / 1e6),
        'freq_ppm': float((k - 1.0) * 1e6),
    }
    return k, b, diag


def load_robot_arms(session_dir: Path) -> dict[int, pd.DataFrame]:
    """Load per-arm robot pose dataframes, keyed by arm id (1..4)."""
    arms: dict[int, pd.DataFrame] = {}
    pose_root = session_dir / 'robot_pose'

    # Preferred multi-arm layout.
    for arm_id in range(1, 5):
        path = pose_root / f'robot_pose_arm{arm_id}' / 'twincat_poses.csv'
        if path.exists():
            df = pd.read_csv(path)
            df = df.sort_values('timestamp_ns').reset_index(drop=True)
            arms[arm_id] = df
            print(f'robot_pose arm{arm_id}: {len(df)} rows from {path}')

    if arms:
        return arms

    # Legacy single CSV, possibly containing an `arm` column.
    legacy = pose_root / 'twincat_poses.csv'
    if legacy.exists():
        df = pd.read_csv(legacy)
        df = df.sort_values('timestamp_ns').reset_index(drop=True)
        if 'arm' in df.columns:
            for arm_id in sorted(df['arm'].unique()):
                arms[int(arm_id)] = df[df['arm'] == arm_id].reset_index(drop=True)
        else:
            arms[1] = df
        print(f'robot_pose (legacy): {len(df)} rows, arms={sorted(arms)}')
        return arms

    raise FileNotFoundError(f'No robot pose CSV found under {pose_root}')


def unwrap_quaternions(quat: np.ndarray) -> np.ndarray:
    """Flip quaternion signs so consecutive quats take the short SLERP arc."""
    out = quat.copy()
    for i in range(1, len(out)):
        if np.dot(out[i], out[i - 1]) < 0.0:
            out[i] = -out[i]
    return out


def slerp(q0: np.ndarray, q1: np.ndarray, t: float) -> np.ndarray:
    """Spherical linear interpolation between two unit quaternions."""
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    dot = np.clip(dot, -1.0, 1.0)
    if dot > 0.9995:
        q = q0 + t * (q1 - q0)
        return q / np.linalg.norm(q)
    theta = np.arccos(dot)
    sin_theta = np.sin(theta)
    return (np.sin((1 - t) * theta) * q0 + np.sin(t * theta) * q1) / sin_theta


def nearest_idx(ts: np.ndarray, t: float) -> int:
    """Index of the sample in sorted ts nearest to t."""
    idx = int(np.searchsorted(ts, t))
    idx = int(np.clip(idx, 1, len(ts) - 1))
    return idx - 1 if abs(t - ts[idx - 1]) <= abs(t - ts[idx]) else idx


def interp_arm_pose(arm_df: pd.DataFrame, t: float) -> dict | None:
    """Interpolate one arm's pose at PLC time t. None if outside range."""
    ts = arm_df['timestamp_ns'].values.astype(np.float64)
    if t < ts[0] or t > ts[-1]:
        return None

    idx = int(np.searchsorted(ts, t))
    idx = int(np.clip(idx, 1, len(ts) - 1))
    t0, t1 = ts[idx - 1], ts[idx]
    alpha = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)

    out: dict[str, float] = {}

    for col in POSE_COLS:
        if col in arm_df.columns:
            out[col] = float(np.interp(t, ts, arm_df[col].values.astype(np.float64)))

    if all(c in arm_df.columns for c in ORIENT_COLS):
        quat = unwrap_quaternions(arm_df[ORIENT_COLS].values.astype(np.float64))
        q = slerp(quat[idx - 1], quat[idx], alpha)
        for c, v in zip(ORIENT_COLS, q):
            out[c] = float(v)

    for col in LERP_COLS:
        if col in arm_df.columns:
            out[col] = float(np.interp(t, ts, arm_df[col].values.astype(np.float64)))

    joint_cols = [c for c in arm_df.columns if c.startswith('joint_')]
    for col in joint_cols:
        out[col] = float(np.interp(t, ts, arm_df[col].values.astype(np.float64)))

    near = nearest_idx(ts, t)
    for col in NEAREST_COLS:
        if col in arm_df.columns:
            out[col] = arm_df[col].iloc[near]

    return out


def build_camera_anchor_alignment(
    session_dir: Path,
    anchor: str,
    other_cameras: list[str],
    include_joints: bool,
) -> tuple[pd.DataFrame, dict]:
    """One output row per anchor-camera frame, with interpolated arm poses."""
    arms = load_robot_arms(session_dir)

    # Fit the PLC-PC drift model on the arm with the most samples.
    ref_arm = max(arms, key=lambda a: len(arms[a]))
    k, b, diag = fit_plc_pc_model(arms[ref_arm])
    print(f'PLC-PC drift model fitted on arm{ref_arm}: k={k:.12f}, b={b:.0f} ns, '
          f'residual std={diag["residual_std_ms"]:.3f} ms, max={diag["residual_max_abs_ms"]:.3f} ms')

    # Load camera metadata; timestamps mapped to the PLC axis.
    cameras: dict[str, dict] = {}
    for name in [anchor, *other_cameras]:
        meta_path = session_dir / name / 'metadata.csv'
        if not meta_path.exists():
            print(f'{name}: metadata.csv not found, skipping')
            continue
        df = pd.read_csv(meta_path)
        # Multi-writer workers may interleave metadata rows; sort by timestamp.
        df = df.sort_values('timestamp_ns').reset_index(drop=True)
        pc = df['timestamp_ns'].values.astype(np.float64)
        cameras[name] = {
            'df': df,
            'plc': k * pc + b,
        }
    if anchor not in cameras:
        raise FileNotFoundError(f'Anchor camera metadata not found: {anchor}')

    anchor_cam = cameras[anchor]
    robot_ts_min = min(arm['timestamp_ns'].min() for arm in arms.values())
    robot_ts_max = max(arm['timestamp_ns'].max() for arm in arms.values())

    rows = []
    for i, frame_row in anchor_cam['df'].iterrows():
        t_plc = float(anchor_cam['plc'][i])
        out_row = {
            'anchor_frame_idx': int(frame_row['index']),
            'anchor_filename': frame_row['filename'],
            'anchor_timestamp_ns_pc': int(frame_row['timestamp_ns']),
            'anchor_timestamp_ns_plc': int(round(t_plc)),
            'pose_valid': robot_ts_min <= t_plc <= robot_ts_max,
        }

        all_cams_valid = True
        for name in other_cameras:
            if name not in cameras:
                continue
            cam = cameras[name]
            cam_plc = cam['plc']
            in_range = cam_plc[0] <= t_plc <= cam_plc[-1]
            j = nearest_idx(cam_plc, t_plc)
            out_row[f'{name}_frame_idx'] = int(cam['df'].iloc[j]['index'])
            out_row[f'{name}_filename'] = cam['df'].iloc[j]['filename']
            out_row[f'{name}_error_ms'] = abs(cam_plc[j] - t_plc) / 1e6
            out_row[f'{name}_in_range'] = in_range
            all_cams_valid = all_cams_valid and in_range
        out_row['all_cameras_valid'] = all_cams_valid

        if out_row['pose_valid']:
            for arm_id, arm_df in arms.items():
                pose = interp_arm_pose(arm_df, t_plc)
                if pose is None:
                    continue
                for col, val in pose.items():
                    if not include_joints and col.startswith('joint_'):
                        continue
                    out_row[f'arm{arm_id}_{col}'] = val

        rows.append(out_row)

    df = pd.DataFrame(rows)
    stats = {
        'drift': diag,
        'anchor_rows': len(df),
        'pose_valid_rows': int(df['pose_valid'].sum()),
    }
    return df, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Camera-anchored alignment with pose interpolation (SRT-H style)'
    )
    parser.add_argument('--session', type=Path, required=True, help='Session directory')
    parser.add_argument('--anchor', type=str, default='camera_arm_2',
                        help='Anchor camera directory name (30 fps reference)')
    parser.add_argument('--cameras', type=str, nargs='*',
                        default=['camera_arm_1', 'camera_arm_3'],
                        help='Other cameras to align to the anchor')
    parser.add_argument('--include-joints', action='store_true',
                        help='Also interpolate joint_1..joint_12 columns')
    parser.add_argument('--out-dir', type=Path, default=None, help='Output directory')
    args = parser.parse_args()

    session_dir = args.session
    out_dir = args.out_dir or session_dir / 'camera_anchor_alignment'
    out_dir.mkdir(parents=True, exist_ok=True)

    df, stats = build_camera_anchor_alignment(
        session_dir, args.anchor, args.cameras, args.include_joints,
    )

    out_path = out_dir / 'camera_anchor_alignment.csv'
    df.to_csv(out_path, index=False)

    valid = df[df['pose_valid'] & df['all_cameras_valid']]
    print(f'\nCamera-anchor alignment saved: {out_path}')
    print(f'Anchor frames: {stats["anchor_rows"]}, with interpolated pose: '
          f'{stats["pose_valid_rows"]} ({100 * stats["pose_valid_rows"] / max(stats["anchor_rows"], 1):.1f}%)')
    print(f'Rows with pose + all cameras in range: {len(valid)} '
          f'({100 * len(valid) / max(stats["anchor_rows"], 1):.1f}%)')

    valid_path = out_dir / 'camera_anchor_alignment_valid.csv'
    valid.to_csv(valid_path, index=False)
    print(f'Valid-only CSV saved: {valid_path}')

    for name in args.cameras:
        col = f'{name}_error_ms'
        if col in valid.columns:
            err = valid[col].values
            print(f'\n=== {name} nearest-frame error to anchor (valid rows) ===')
            print(f'  Mean: {err.mean():.3f} ms, Std: {err.std():.3f} ms, '
                  f'Max: {err.max():.3f} ms, Median: {np.median(err):.3f} ms, '
                  f'P95: {np.percentile(err, 95):.3f} ms')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
