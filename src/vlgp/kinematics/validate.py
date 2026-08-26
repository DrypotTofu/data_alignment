"""T16 real-data validation: FK vs PLC pose, Kabsch base->scope, data sanity.

Run after any session with live PLC data (robot_pose_arm*/twincat_poses.csv):

    python -m vlgp.kinematics.validate --session sessions/<name>

Writes ``<session>/t16_validation.json`` and prints a summary.

What this checks on a real session
----------------------------------
1. Data sanity: quaternion unit norm, joint stability (robot idle or moving),
   position stability, scope-position stability.
2. FK vs PLC under the current joint->MDH hypothesis (``kinematics.fk``):
   per-arm position/rotation error. With the default hypothesis the error is
   expected to be large until the joint mapping / zero offsets / sign
   conventions are resolved against live PLC output.
3. Kabsch base->scope: the PLC publishes end-effector positions in the arm
   (base) frame and in the scope frame; if both are in frames related by one
   constant rigid transform, the Kabsch residual RMS should be ~0. A large
   residual means per-arm base frames or a PLC computation detail to clarify.

The FK-vs-PLC part is meant to be re-run after a controlled single-joint
motion session (one joint at a time, known increments) so the joint->MDH-row
mapping, offsets and signs can be resolved; see
tech_repo/T16_FK与PLC对标验证_20260821.md for the protocol.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial.transform import Rotation as Rot

from . import fk
from .transform import kabsch

ARMS = (1, 2, 3, 4)
JOINT_COLS = [f'joint_{k}' for k in range(1, 13)]


def load_arm(session_dir: Path, arm: int) -> pd.DataFrame | None:
    csv = (Path(session_dir) / 'robot_pose' / f'robot_pose_arm{arm}'
           / 'twincat_poses.csv')
    return pd.read_csv(csv) if csv.exists() else None


def plc_rotation_matrix(row: pd.Series) -> np.ndarray:
    """PLC quaternion (w,x,y,z columns) -> rotation matrix (scipy xyzw)."""
    q = np.array([row['orientation_x'], row['orientation_y'],
                  row['orientation_z'], row['orientation_w']])
    return Rot.from_quat(q).as_matrix()


def validate_session(session_dir: Path, subsample: int = 30) -> dict:
    """Run the T16 checks and return a JSON-serialisable report."""
    report: dict = {
        'session': str(session_dir),
        'arms': {},
        'kabsch_base_to_scope': None,
    }
    A_all: list[np.ndarray] = []
    B_all: list[np.ndarray] = []
    any_arm = False

    for arm in ARMS:
        df = load_arm(session_dir, arm)
        if df is None or df.empty:
            continue
        any_arm = True
        q = df[['orientation_w', 'orientation_x', 'orientation_y',
                'orientation_z']].values.astype(np.float64)
        q_norm = np.linalg.norm(q, axis=1)
        joints = df[JOINT_COLS].values.astype(np.float64)
        pos = df[['position_x', 'position_y', 'position_z']].values
        scope = df[['scope_position_x', 'scope_position_y',
                    'scope_position_z']].values
        # FK vs PLC at the last row (joints effectively constant for an
        # idle robot; for a moving session a per-sample sweep is better, but
        # the mapping hypothesis must be right first).
        row = df.iloc[-1]
        p_fk, R_fk = fk(arm, [float(v) for v in joints[-1]])
        p_plc = pos[-1]
        R_plc = plc_rotation_matrix(row)
        pos_err = float(np.linalg.norm(p_fk - p_plc))
        rel = R_plc @ R_fk.T
        rot_err = float(np.degrees(np.arccos(
            np.clip((np.trace(rel) - 1) / 2, -1, 1))))
        a = {
            'n_rows': int(len(df)),
            'quat_norm_min': float(q_norm.min()),
            'quat_norm_max': float(q_norm.max()),
            'joint_std_max': float(joints.std(axis=0).max()),
            'pos_step_max_mm': float(np.linalg.norm(
                np.diff(pos, axis=0), axis=1).max()),
            'scope_pos_mean_mm': scope.mean(axis=0).round(3).tolist(),
            'scope_pos_std_mm': scope.std(axis=0).round(3).tolist(),
            'joints_last': [round(float(v), 4) for v in joints[-1]],
            'fk_vs_plc_pos_err_mm': round(pos_err, 3),
            'fk_vs_plc_rot_err_deg': round(rot_err, 3),
        }
        report['arms'][f'arm{arm}'] = a
        sub = df.iloc[::subsample]
        A_all.append(sub[['position_x', 'position_y', 'position_z']]
                    .values.astype(np.float64))
        B_all.append(sub[['scope_position_x', 'scope_position_y',
                          'scope_position_z']]
                    .values.astype(np.float64))

    if any_arm and A_all:
        A = np.vstack(A_all)
        B = np.vstack(B_all)
        R, t, rms = kabsch(A, B)
        rot_deg = float(np.degrees(np.arccos(
            np.clip((np.trace(R) - 1) / 2, -1, 1))))
        per_arm_resid = {}
        for arm in ARMS:
            idx = arm - 1
            if idx >= len(A_all):
                continue
            r = B_all[idx] - (A_all[idx] @ R.T + t)
            per_arm_resid[f'arm{arm}'] = {
                'rms_mm': round(float(np.sqrt((r ** 2).sum(1).mean())), 3),
                'max_mm': round(float(np.sqrt((r ** 2).sum(1)).max()), 3),
            }
        report['kabsch_base_to_scope'] = {
            'n_pairs': int(len(A)),
            'R_bs': R.round(5).tolist(),
            't_bs_mm': t.round(3).tolist(),
            'rms_mm': round(rms, 3),
            'rot_deg': round(rot_deg, 3),
            'per_arm_resid_mm': per_arm_resid,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description='T16 real-data validation (FK vs PLC, Kabsch, sanity)')
    parser.add_argument('--session', type=Path, required=True,
                        help='Session directory containing robot_pose/')
    parser.add_argument('--subsample', type=int, default=30,
                        help='Row stride for Kabsch pairing (default 30)')
    args = parser.parse_args()

    report = validate_session(args.session, args.subsample)
    out = Path(args.session) / 't16_validation.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding='utf-8')

    print(f'=== T16 validation: {args.session} ===')
    for arm, a in report['arms'].items():
        print(f'  {arm}: n={a["n_rows"]} |q|=[{a["quat_norm_min"]:.6f},'
              f'{a["quat_norm_max"]:.6f}] joint_std_max={a["joint_std_max"]:.4f} '
              f'| FK-vs-PLC pos={a["fk_vs_plc_pos_err_mm"]} mm '
              f'rot={a["fk_vs_plc_rot_err_deg"]} deg')
    k = report['kabsch_base_to_scope']
    if k:
        print(f'  Kabsch base->scope: n={k["n_pairs"]} rms={k["rms_mm"]} mm '
              f'rot={k["rot_deg"]} deg')
        for arm, r in k['per_arm_resid_mm'].items():
            print(f'    {arm}: resid rms={r["rms_mm"]} mm max={r["max_mm"]} mm')
    else:
        print('  (no usable paired positions)')
    print(f'  report -> {out}')


if __name__ == '__main__':
    main()
