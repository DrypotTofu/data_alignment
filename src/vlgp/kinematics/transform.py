"""Base->scope rigid transform via Kabsch/Umeyama registration (T16).

The PLC publishes the end-effector position of every arm in two frames:
    position_*       (arm/base frame)
    scope_position_* (scope frame, endoscope fixed)
With the endoscope fixed, the two point sets differ by a constant rigid
transform (R_bs, t_bs). This module solves it from paired samples by
least-squares (Kabsch) and reports the residual RMS.

Requires session data where arms moved; run `solve_from_session` on >=2
sessions to cross-validate consistency (rot < 1 deg, trans < 5 mm, per T16).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ARMS = (1, 2, 3, 4)


def kabsch(A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Least-squares rigid transform mapping A -> B:  B ~= R @ A + t.

    A, B: (N, 3) paired point sets. Returns (R, t, rms_mm).
    """
    A = np.asarray(A, dtype=np.float64)
    B = np.asarray(B, dtype=np.float64)
    assert A.shape == B.shape and A.ndim == 2 and A.shape[1] == 3
    cA = A.mean(axis=0)
    cB = B.mean(axis=0)
    H = (A - cA).T @ (B - cB)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    t = cB - R @ cA
    resid = B - (A @ R.T + t)
    rms = float(np.sqrt((resid ** 2).sum(axis=1).mean()))
    return R, t, rms


def self_check() -> dict[str, str]:
    """Verify Kabsch recovers a known rigid transform under small noise."""
    rng = np.random.default_rng(42)
    from scipy.spatial.transform import Rotation as Rot
    R_true = Rot.from_euler('xyz', [10, -20, 35], degrees=True).as_matrix()
    t_true = np.array([150.0, -80.0, 200.0])
    A = rng.uniform(-100, 100, size=(500, 3))
    B = A @ R_true.T + t_true + rng.normal(0, 0.5, size=A.shape)
    R, t, _ = kabsch(A, B)
    rel = R @ R_true.T
    rot_err = float(np.degrees(np.arccos(np.clip((np.trace(rel) - 1) / 2, -1, 1))))
    trans_err = float(np.linalg.norm(t - t_true))
    results = {
        'kabsch_rot_err_deg': 'PASS' if rot_err < 0.05 else 'FAIL',
        'kabsch_trans_err_mm': 'PASS' if trans_err < 0.5 else 'FAIL',
    }
    return results


def paired_points_from_session(session_dir: Path) -> np.ndarray | None:
    """Collect (base_pos, scope_pos) pairs from a session's arm CSVs.

    Returns (A, B) arrays of shape (N, 3) for all arms with both columns, or
    None if no usable pairs.
    """
    pairs_a: list[np.ndarray] = []
    pairs_b: list[np.ndarray] = []
    rp = Path(session_dir) / 'robot_pose'
    csvs = sorted(rp.glob('robot_pose_arm*/twincat_poses.csv')) if rp.exists() else []
    for csv in csvs:
        try:
            df = pd.read_csv(csv)
        except Exception:
            continue
        if 'position_x' not in df or 'scope_position_x' not in df:
            continue
        # Subsample to avoid redundancy (every 30th row ~ 1 Hz at 30 Hz rec).
        sub = df.iloc[::30]
        a = sub[['position_x', 'position_y', 'position_z']].values.astype(np.float64)
        b = sub[['scope_position_x', 'scope_position_y', 'scope_position_z']].values.astype(np.float64)
        mask = np.all(np.isfinite(a), axis=1) & np.all(np.isfinite(b), axis=1)
        if mask.sum() > 0:
            pairs_a.append(a[mask])
            pairs_b.append(b[mask])
    if not pairs_a:
        return None
    return np.vstack(pairs_a), np.vstack(pairs_b)


def solve_from_session(session_dir: Path) -> dict:
    """Solve R_bs/t_bs from one session and report residuals."""
    pts = paired_points_from_session(session_dir)
    if pts is None:
        return {'error': 'no usable paired positions in session'}
    A, B = pts
    R, t, rms = kabsch(A, B)
    rot_deg = float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))))
    return {
        'n_pairs': int(len(A)),
        'R_bs': R.tolist(),
        't_bs_mm': t.tolist(),
        'rms_mm': float(rms),
        'rot_deg': rot_deg,
    }


def cross_validate(sessions: list[Path]) -> dict:
    """Solve per session and compare consistency (T16 acceptance)."""
    sols = []
    for s in sessions:
        sol = solve_from_session(s)
        if 'error' in sol:
            continue
        sol['session'] = str(s)
        sols.append(sol)
    if len(sols) < 2:
        return {'error': f'need >=2 usable sessions, got {len(sols)}', 'solutions': sols}
    # Pairwise differences of R (axis-angle) and t.
    diffs = []
    for i in range(len(sols)):
        for j in range(i + 1, len(sols)):
            Ri = np.array(sols[i]['R_bs'])
            Rj = np.array(sols[j]['R_bs'])
            rel = Rj @ Ri.T
            rot_deg = float(np.degrees(np.arccos(np.clip((np.trace(rel) - 1) / 2, -1, 1))))
            ti = np.array(sols[i]['t_bs_mm'])
            tj = np.array(sols[j]['t_bs_mm'])
            diffs.append({'pair': (i, j), 'rot_diff_deg': rot_deg,
                          'trans_diff_mm': float(np.linalg.norm(tj - ti))})
    return {'solutions': sols, 'pairwise': diffs}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='T16 Kabsch base->scope transform')
    parser.add_argument('--session', nargs='+', type=Path, required=True,
                        help='One or more session dirs for cross-validation')
    args = parser.parse_args()
    if len(args.session) == 1:
        print(json.dumps(solve_from_session(args.session[0]), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(cross_validate(args.session), ensure_ascii=False, indent=2))
