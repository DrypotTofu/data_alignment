"""Build a SRT-H-style training episode from a camera-anchored alignment table.

Pipeline position (AGENT_HANDOFF.md T2 / tech_repo 13.5):

    raw session --align_camera_anchor.py--> camera_anchor_alignment.csv
        --build_srth_dataset.py--> srth_episode/

The episode is the sample unit of the SRT-H-style low-level policy: one 30 Hz
anchor frame + three synchronised images + a future 60-step (2 s @ 30 Hz)
40-dim action chunk per arm.

Action space (per arm, 4 arms -> 40 dims, SRT-H style):
    delta_pos      3   scope-frame (``scope_position_*``) position delta
    delta_rot_6d   6   first two columns of the relative rotation matrix
                        R_rel = R(q_{i+1}) @ R(q_i)^T, stored as [g1; g2]
                        (Zhou et al. 2019 convention: flat [R00,R10,R20,R01,R11,R21])
    jaw            1   ``gripper_angle`` normalised to [0, 1]
plus a per-arm boolean ``arm_active_mask`` (heuristic, see below).

The per-sample 60-step chunk (``action_chunks.parquet``) follows the same
per-arm layout: [arm1 pos3|rot6|jaw, arm2 ..., arm3 ..., arm4 ...] = [60, 40].
Chunk cells are pyarrow ``list<list<double>>``; load with ``np.stack(cell)``.

Coordinate / quaternion facts (confirmed 2026-08-16):
  * PLC quaternions are **wxyz (scalar-first)**; scipy wants xyzw, so every
    quaternion read from the alignment CSV is reordered ``q[[1, 2, 3, 0]]``
    before being handed to ``scipy.spatial.transform.Rotation``.
  * The PLC publishes scope-frame *positions* only (``scope_position_*``);
    there is no scope-frame orientation symbol. ``delta_rot_6d`` is therefore
    computed from the base-frame orientation quaternions. For small per-step
    rotations the rotation *angle* is identical in both frames (conjugation by
    the fixed scope<->base transform preserves angle); the *axis* differs by
    that unknown constant rotation. This is a documented, reported limitation
    (see ``meta.json``), not a silent coordinate fix.
  * Base-frame poses are kept in ``actions.parquet`` and ``meta.json`` for
    workspace checks / visualisation.

Tail handling for action chunks (documented decision):
  Samples whose chunk would extend past the episode end (fewer than 60 future
  steps) are **dropped** — no zero padding, no partial chunks. This loses at
  most the final 2 s of an episode and avoids injecting fake "stop" actions
  into the chunk horizon. Counts are reported in ``meta.json``.

``arm_active_mask`` is a placeholder heuristic until real task demos define
the convention: an arm counts as active over its chunk if it exceeds a motion
threshold (default 0.05 mm/step position or 0.1 deg/step rotation). Static
arms (and the endoscope holder when static) come out inactive. Thresholds are
CLI-tunable (``--mask-pos-mm`` / ``--mask-rot-deg``).

Unit self-tests (quaternion reorder / identity / 90 deg rotation / SLERP /
relative rotation) run automatically at startup; ``--skip-self-test`` disables.

Usage:
    python scripts/tools/build_srth_dataset.py --session sessions/test_xxx
    python scripts/tools/build_srth_dataset.py --session sessions/test_xxx --no-copy
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ARMS = (1, 2, 3, 4)
ARM_KEYS = [f'arm{a}' for a in ARMS]
POS_COLS = [f'arm{a}_{c}' for a in ARMS for c in ('position_x', 'position_y', 'position_z')]
SCOPE_COLS = [f'arm{a}_scope_position_{c}' for a in ARMS
              for c in ('x', 'y', 'z')]
ORIENT_COLS = [f'arm{a}_orientation_{c}' for a in ARMS
               for c in ('w', 'x', 'y', 'z')]
GRIPPER_COLS = [f'arm{a}_gripper_angle' for a in ARMS]
INSTRUMENT_COLS = [f'arm{a}_instrument_type' for a in ARMS]


# ---------------------------------------------------------------------------
# Quaternion helpers and unit self-tests
# ---------------------------------------------------------------------------

def wxyz_to_xyzw(q: np.ndarray) -> np.ndarray:
    """Reorder PLC quaternions from wxyz (scalar-first) to scipy xyzw."""
    return np.asarray(q)[..., [1, 2, 3, 0]]


def rotation_matrix(quat_xyzw: np.ndarray) -> np.ndarray:
    """Unit quaternion (..., 4, xyzw) -> rotation matrix (..., 3, 3)."""
    from scipy.spatial.transform import Rotation
    return Rotation.from_quat(np.asarray(quat_xyzw, dtype=np.float64)).as_matrix()


def slerp_quat(q0_xyzw: np.ndarray, q1_xyzw: np.ndarray, t: float) -> np.ndarray:
    """Spherical linear interpolation (xyzw quats) — own implementation.

    scipy >= 1.17 removed ``Rotation.slerp``, so the same formula used by
    ``align_camera_anchor.py`` lives here (used by the unit self-test).
    """
    q0 = np.asarray(q0_xyzw, dtype=np.float64)
    q1 = np.asarray(q1_xyzw, dtype=np.float64)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    dot = np.clip(dot, -1.0, 1.0)
    if dot > 0.9995:
        q = q0 + t * (q1 - q0)
        return q / np.linalg.norm(q)
    theta = np.arccos(dot)
    return (np.sin((1 - t) * theta) * q0 + np.sin(t * theta) * q1) / np.sin(theta)


def relative_rot_6d(q0_wxyz: np.ndarray, q1_wxyz: np.ndarray) -> np.ndarray:
    """6D action label from two consecutive PLC quaternions (wxyz).

    R_rel = R(q1) @ R(q0)^T; return the first two columns concatenated as
    [g1; g2] with g1 = R[:, 0], g2 = R[:, 1] (Zhou et al. 2019 convention),
    i.e. flat [R00, R10, R20, R01, R11, R21].
    """
    r0 = rotation_matrix(wxyz_to_xyzw(q0_wxyz))
    r1 = rotation_matrix(wxyz_to_xyzw(q1_wxyz))
    rel = r1 @ r0.T
    return np.concatenate([rel[:, 0], rel[:, 1]])


def rotation_angle_deg(q0_wxyz: np.ndarray, q1_wxyz: np.ndarray) -> float:
    """Angle of the relative rotation R(q1) @ R(q0)^T, in degrees."""
    r0 = rotation_matrix(wxyz_to_xyzw(q0_wxyz))
    r1 = rotation_matrix(wxyz_to_xyzw(q1_wxyz))
    rel = r1 @ r0.T
    cos_theta = float(np.clip((np.trace(rel) - 1.0) / 2.0, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_theta)))


def _assert_close(name: str, got: np.ndarray, want: np.ndarray, tol: float = 1e-6):
    if not np.allclose(got, want, atol=tol):
        raise AssertionError(f'self-test FAILED: {name}\n got={got}\n want={want}')


def run_self_tests() -> dict:
    """Validate quaternion handling against known results (run at startup)."""
    results: dict[str, str] = {}
    s2 = np.sqrt(0.5)

    # 1. wxyz -> xyzw reorder
    q_wxyz = np.array([0.1, 0.2, 0.3, 0.4])
    q_xyzw = wxyz_to_xyzw(q_wxyz)
    _assert_close('wxyz->xyzw reorder', q_xyzw, np.array([0.2, 0.3, 0.4, 0.1]))
    results['reorder_wxyz_to_xyzw'] = 'PASS'

    # 2. Identity quaternion (xyzw [0,0,0,1]) -> identity matrix
    _assert_close('identity quat', rotation_matrix([0, 0, 0, 1]), np.eye(3))
    results['identity_rotation'] = 'PASS'

    # 3. Known 90 deg rotation about +Z (xyzw [0,0,sin45,cos45])
    r90 = rotation_matrix([0.0, 0.0, s2, s2])
    want_rz90 = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    _assert_close('90deg_about_Z', r90, want_rz90)
    results['known_90deg_rotation'] = 'PASS'

    # 4. SLERP half-way between identity and +90deg-Z is +45deg-Z.
    #    (scipy removed Rotation.slerp in 1.17; own slerp_quat used, xyzw.)
    half = slerp_quat([0.0, 0.0, 0.0, 1.0], [0.0, 0.0, s2, s2], 0.5)
    want_half = np.array([0.0, 0.0, np.sin(np.pi / 8), np.cos(np.pi / 8)])
    _assert_close('slerp_half_45deg', half, want_half)
    results['slerp_half_45deg'] = 'PASS'

    # 5. Relative rotation: from +90deg-Z to identity == -90deg about Z
    rel = rotation_matrix([0.0, 0.0, 0.0, 1.0]) @ rotation_matrix([0.0, 0.0, s2, s2]).T
    _assert_close('relative_rot_-90deg', rel, want_rz90.T)
    results['relative_rotation'] = 'PASS'

    # 6. relative_rot_6d on a known pair (wxyz input): R(-90deg about Z)
    six = relative_rot_6d(np.array([s2, 0.0, 0.0, s2]), np.array([1.0, 0.0, 0.0, 0.0]))
    want_rel = want_rz90.T  # -90 deg about Z
    want_six = np.concatenate([want_rel[:, 0], want_rel[:, 1]])  # [g1; g2]
    _assert_close('relative_rot_6d_known', six, want_six)
    results['relative_rot_6d'] = 'PASS'

    return results


# ---------------------------------------------------------------------------
# Action computation
# ---------------------------------------------------------------------------

def compute_actions(align: pd.DataFrame) -> pd.DataFrame:
    """Per-step action labels on the (kept) 30 Hz grid.

    Row i carries the transition from state i to state i+1:
      delta_pos    = scope_pos[i+1] - scope_pos[i]          (scope frame, mm)
      delta_rot_6d = first two columns of R(q[i+1]) @ R(q[i])^T
      jaw          = normalised gripper_angle at i+1
    The last row (no successor) is dropped.
    """
    rows = []
    quats = {a: align[ORIENT_COLS].loc[:, [f'arm{a}_orientation_w',
                                           f'arm{a}_orientation_x',
                                           f'arm{a}_orientation_y',
                                           f'arm{a}_orientation_z']].values
             for a in ARMS}
    scope = {a: align[SCOPE_COLS].loc[:, [f'arm{a}_scope_position_x',
                                          f'arm{a}_scope_position_y',
                                          f'arm{a}_scope_position_z']].values
             for a in ARMS}

    for i in range(len(align) - 1):
        row = dict(align.iloc[i])
        for a in ARMS:
            dpos = scope[a][i + 1] - scope[a][i]
            row[f'arm{a}_delta_pos_x'] = dpos[0]
            row[f'arm{a}_delta_pos_y'] = dpos[1]
            row[f'arm{a}_delta_pos_z'] = dpos[2]
            six = relative_rot_6d(quats[a][i], quats[a][i + 1])
            for k in range(6):
                row[f'arm{a}_delta_rot_6d_{k}'] = six[k]
            row[f'arm{a}_rot_angle_deg'] = rotation_angle_deg(quats[a][i], quats[a][i + 1])
        rows.append(row)

    return pd.DataFrame(rows)


def normalise_jaw(actions: pd.DataFrame, lo: float, hi: float,
                  invert: bool) -> pd.DataFrame:
    """gripper_angle -> jaw in [0, 1] (clip; optional invert)."""
    out = actions.copy()
    for a in ARMS:
        ga = out[f'arm{a}_gripper_angle'].values.astype(float)
        jaw = np.clip((ga - lo) / (hi - lo), 0.0, 1.0)
        if invert:
            jaw = 1.0 - jaw
        out[f'arm{a}_jaw'] = jaw
    return out


def motion_active_mask(actions: pd.DataFrame, pos_mm: float, rot_deg: float) -> pd.DataFrame:
    """Per-step arm-active heuristic: any arm exceeding a motion threshold."""
    out = actions.copy()
    for a in ARMS:
        dp = np.sqrt(np.sum([
            out[f'arm{a}_delta_pos_{c}'].values ** 2 for c in ('x', 'y', 'z')
        ], axis=0))
        out[f'arm{a}_active'] = (dp > pos_mm) | (out[f'arm{a}_rot_angle_deg'].values > rot_deg)
    return out


# ---------------------------------------------------------------------------
# Episode writer
# ---------------------------------------------------------------------------

def _copy_frames(session_dir: Path, cam_name: str, frame_files: list[str],
                 out_dir: Path, manifest: dict, copy: bool) -> list[str]:
    """Copy/register the kept frames of one camera into obs/<stream>/."""
    stream = {'camera_arm_2': 'endoscope', 'camera_arm_1': 'wrist_left',
              'camera_arm_3': 'wrist_right'}.get(cam_name, cam_name)
    dst = out_dir / 'obs' / stream
    dst.mkdir(parents=True, exist_ok=True)
    src_dir = session_dir / cam_name
    kept = []
    for i, fname in enumerate(frame_files):
        out_name = f'frame_{i:06d}.{Path(fname).suffix.lstrip(".")}'
        if copy:
            shutil.copy2(src_dir / fname, dst / out_name)
        kept.append(out_name)
        manifest.setdefault(stream, {})[out_name] = fname
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Build a SRT-H-style episode from a camera-anchored alignment table')
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--alignment', type=Path, default=None,
                        help='Alignment CSV (default: <session>/camera_anchor_alignment/'
                             'camera_anchor_alignment.csv)')
    parser.add_argument('--out', type=Path, default=None,
                        help='Episode output dir (default: <session>/srth_episode)')
    parser.add_argument('--error-threshold-ms', type=float, default=15.0,
                        help='Keep only samples where every wrist camera nearest-frame '
                             'error is below this (default 15 ms, see 13.5)')
    parser.add_argument('--chunk-steps', type=int, default=60,
                        help='Future action-chunk length in 30 Hz steps (default 60 = 2 s)')
    parser.add_argument('--hl-interval-s', type=float, default=1.0,
                        help='HL policy sample spacing in seconds (default 1 s)')
    parser.add_argument('--hl-window', type=int, default=5,
                        help='HL frames per sample: current + (window-1) past frames')
    parser.add_argument('--jaw-lo', type=float, default=-60.0)
    parser.add_argument('--jaw-hi', type=float, default=90.0)
    parser.add_argument('--jaw-invert', action='store_true',
                        help='Larger gripper_angle = more closed (flips jaw)')
    parser.add_argument('--mask-pos-mm', type=float, default=0.05)
    parser.add_argument('--mask-rot-deg', type=float, default=0.1)
    parser.add_argument('--no-copy', action='store_true',
                        help='Do not copy images; write obs manifests only (fast)')
    parser.add_argument('--skip-self-test', action='store_true')
    args = parser.parse_args()

    session_dir = args.session
    if not session_dir.exists():
        print(f'ERROR: session not found: {session_dir}')
        return 1
    align_path = args.alignment or (
        session_dir / 'camera_anchor_alignment' / 'camera_anchor_alignment.csv')
    if not align_path.exists():
        print(f'ERROR: alignment CSV not found: {align_path}')
        return 1

    # ---- unit self-tests ------------------------------------------------
    self_tests = {} if args.skip_self_test else run_self_tests()
    if not args.skip_self_test:
        print('=== quaternion unit self-tests ===')
        for k, v in self_tests.items():
            print(f'  {k}: {v}')

    # ---- load + filter --------------------------------------------------
    align = pd.read_csv(align_path)
    align = align.sort_values('anchor_timestamp_ns_plc').reset_index(drop=True)
    # Keep the original alignment-CSV row index for T10 trim mapping.
    align['_src_row'] = align.index
    total_rows = len(align)
    with_pose = align[align['pose_valid']].copy()
    with_all_cams = with_pose[with_pose['all_cameras_valid']].copy()
    err_cols = [c for c in with_all_cams.columns if c.endswith('_error_ms')
                and c.startswith('camera_')]
    mask_err = np.ones(len(with_all_cams), dtype=bool)
    for c in err_cols:
        mask_err &= with_all_cams[c].fillna(1e9) < args.error_threshold_ms
    grid = with_all_cams[mask_err].reset_index(drop=True)
    filtered_frac = 1.0 - len(grid) / max(total_rows, 1)

    # ---- annotations.json (T10): reject / trim --------------------------
    # Trim rows are alignment-CSV row indices (0-based, sorted PLC axis);
    # `_src_row` carries the original alignment row through all filters.
    annotations = None
    ann_path = session_dir / 'annotations.json'
    if ann_path.exists():
        annotations = json.loads(ann_path.read_text(encoding='utf-8'))
        print(f'\n=== annotations.json (T10) ===')
        print(f'  status: {annotations.get("status")}')
        if annotations.get('status') == 'rejected':
            print(f'  >>> session REJECTED in review — skipping episode build.')
            print(f'  >>> reason: {annotations.get("reject_reason", "")}')
            return 0
        trim = annotations.get('trim')
        if trim:
            s0, s1 = int(trim['start_row']), int(trim['end_row'])
            keep = (grid['_src_row'] >= s0) & (grid['_src_row'] <= s1)
            grid = grid[keep].reset_index(drop=True)
            print(f'  trim rows [{s0}, {s1}] -> kept {len(grid)} of {total_rows}')

    print(f'\n=== filter summary ===')
    print(f'  alignment rows (anchor frames):        {total_rows}')
    print(f'  rows with pose valid:                  {len(with_pose)}')
    print(f'  rows with all cameras in range:        {len(with_all_cams)}')
    print(f'  rows below {args.error_threshold_ms} ms threshold:          {len(grid)}')
    print(f'  filtered-out fraction:                 {filtered_frac:.3f}')

    if len(grid) < args.chunk_steps + 1:
        print('ERROR: too few samples for one action chunk after filtering; aborting.')
        return 1

    # ---- actions --------------------------------------------------------
    actions = compute_actions(grid)                 # per-step transitions
    actions = normalise_jaw(actions, args.jaw_lo, args.jaw_hi, args.jaw_invert)
    actions = motion_active_mask(actions, args.mask_pos_mm, args.mask_rot_deg)

    # ---- action chunks --------------------------------------------------
    n_chunks = len(actions) - args.chunk_steps + 1  # samples i with i+60-1 < len(actions)
    chunk_rows = []
    for i in range(n_chunks):
        blk = actions.iloc[i:i + args.chunk_steps]
        per_arm = []
        for a in ARMS:
            per_arm.append(np.concatenate([
                blk[[f'arm{a}_delta_pos_{c}' for c in ('x', 'y', 'z')]].values,    # [60, 3]
                blk[[f'arm{a}_delta_rot_6d_{k}' for k in range(6)]].values,        # [60, 6]
                blk[[f'arm{a}_jaw']].values,                                        # [60, 1]
            ], axis=1))                                                             # [60, 10]
        chunk = np.concatenate(per_arm, axis=1)        # [60, 40] per-arm pos3+rot6+jaw
        mask = blk[[f'arm{a}_active' for a in ARMS]].values.astype(bool)
        chunk_rows.append({
            'sample_idx': i,
            'anchor_frame_idx': int(blk.iloc[0]['anchor_frame_idx']),
            'anchor_timestamp_ns_plc': int(blk.iloc[0]['anchor_timestamp_ns_plc']),
            'action_chunk': chunk.tolist(),          # [60, 40] floats
            'arm_active_chunk': mask.tolist(),       # [60, 4] bool
        })
    chunks = pd.DataFrame(chunk_rows)
    tail_dropped = len(grid) - n_chunks

    # ---- HL policy samples ---------------------------------------------
    # Endoscope stream at 1 s spacing; each sample = current frame + (window-1)
    # past frames at 1 s intervals. Obs frame index == grid row index (kept
    # order), so the listed indices address obs/endoscope/frame_%06d directly.
    hl_rows = []
    step = max(1, int(round(args.hl_interval_s * 30.0)))
    need = (args.hl_window - 1) * step
    for i in range(need, len(grid), step):
        idxs = [i - k * step for k in range(args.hl_window)]  # current, -1s, -2s, ...
        hl_rows.append({
            'sample_idx': i,
            'anchor_frame_idx': int(grid.iloc[i]['anchor_frame_idx']),
            'anchor_timestamp_ns_plc': int(grid.iloc[i]['anchor_timestamp_ns_plc']),
            'endoscope_obs_frame_indices': idxs,
        })
    hl = pd.DataFrame(hl_rows)

    # ---- load session metadata (T8: write_session_meta.py sidecar) ------
    session_meta = {}
    session_meta_path = session_dir / 'session_meta.json'
    if session_meta_path.exists():
        session_meta = json.loads(session_meta_path.read_text(encoding='utf-8'))
        print('\n=== session metadata (from session_meta.json) ===')
        for k, v in session_meta.items():
            print(f'  {k}: {v}')
    else:
        print('\n  (no session_meta.json — language fields stay placeholder; '
              'run scripts/tools/write_session_meta.py at collection time)')

    # ---- image registration --------------------------------------------
    out_dir = (args.out or session_dir / 'srth_episode').resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    stream_map = {'camera_arm_2': 'endoscope', 'camera_arm_1': 'wrist_left',
                  'camera_arm_3': 'wrist_right'}
    # The anchor camera's frames live in ``anchor_filename`` (align tool);
    # the other cameras carry ``<camera>_filename`` columns.
    filename_col = {'camera_arm_2': 'anchor_filename',
                    'camera_arm_1': 'camera_arm_1_filename',
                    'camera_arm_3': 'camera_arm_3_filename'}
    kept_files = {}
    for cam, stream in stream_map.items():
        col = filename_col[cam]
        if col not in grid.columns:
            print(f'  WARNING: {col} missing from alignment table; stream {stream} skipped')
            continue
        kept_files[stream] = _copy_frames(
            session_dir, cam, grid[col].tolist(), out_dir, manifest, copy=not args.no_copy)

    # obs frame name for grid row i is frame_{i:06d}.<ext> in kept order
    for stream in ('endoscope', 'wrist_left', 'wrist_right'):
        if stream in kept_files:
            actions[f'{stream}_frame'] = [kept_files[stream][i] for i in range(len(actions))]

    # ---- write artifacts ------------------------------------------------
    actions.to_parquet(out_dir / 'actions.parquet', index=False, compression='zstd')
    chunks.to_parquet(out_dir / 'action_chunks.parquet', index=False, compression='zstd')
    hl.to_parquet(out_dir / 'hl_samples.parquet', index=False, compression='zstd')

    mask_df = actions[['anchor_timestamp_ns_plc'] + [f'arm{a}_active' for a in ARMS]].copy()
    mask_df.to_parquet(out_dir / 'arm_active_mask.parquet', index=False, compression='zstd')

    language = {
        'task_instruction': session_meta.get('task_instruction', ''),
        'corrective_instruction': '',
        'correction_flag': False,
        'note': ('from session_meta.json (task recorded at collection time)'
                 if session_meta
                 else 'placeholder — filled by the annotation step (task demos)'),
    }
    # T8: pass through the task-relevant session metadata fields so the
    # episode's language/meta carry the operator-recorded task info.
    for key in ('task_type', 'subtask_sequence', 'operator', 'organ_model',
                'instruments', 'camera_calibration', 'notes'):
        if key in session_meta:
            language[key] = session_meta[key]
    (out_dir / 'language.json').write_text(json.dumps(
        language, ensure_ascii=False, indent=2), encoding='utf-8')

    # ---- meta.json ------------------------------------------------------
    cam_err = {}
    for c in err_cols:
        s = grid[c].dropna()
        cam_err[c] = {'median_ms': float(s.median()), 'p99_ms': float(s.quantile(0.99)),
                      'max_ms': float(s.max())}
    jaw_stats = {f'arm{a}': {'raw_min': float(grid[f'arm{a}_gripper_angle'].min()),
                             'raw_max': float(grid[f'arm{a}_gripper_angle'].max()),
                             'jaw_min': float(actions[f'arm{a}_jaw'].min()),
                             'jaw_max': float(actions[f'arm{a}_jaw'].max())}
                 for a in ARMS}
    base_pose_stats = {}
    for a in ARMS:
        quat = grid[[f'arm{a}_orientation_{c}' for c in ('w', 'x', 'y', 'z')]].values
        base_pose_stats[f'arm{a}'] = {
            'position_mean_mm': [float(grid[f'arm{a}_position_{c}'].mean()) for c in ('x', 'y', 'z')],
            'position_std_mm': [float(grid[f'arm{a}_position_{c}'].std()) for c in ('x', 'y', 'z')],
            'quat_norm_mean': float(np.sqrt((quat ** 2).sum(axis=1)).mean()),
            'instrument_types': sorted(grid[f'arm{a}_instrument_type'].dropna().unique().tolist()),
        }
    action_ranges = {
        'delta_pos_mm': {'min': float(actions[[f'arm{a}_delta_pos_{c}'
                                               for a in ARMS for c in ('x', 'y', 'z')]].values.min()),
                         'max': float(actions[[f'arm{a}_delta_pos_{c}'
                                               for a in ARMS for c in ('x', 'y', 'z')]].values.max())},
        'delta_rot_6d': {'min': float(actions[[f'arm{a}_delta_rot_6d_{k}'
                                               for a in ARMS for k in range(6)]].values.min()),
                         'max': float(actions[[f'arm{a}_delta_rot_6d_{k}'
                                               for a in ARMS for k in range(6)]].values.max())},
        'jaw': {'min': float(actions[[f'arm{a}_jaw' for a in ARMS]].values.min()),
                'max': float(actions[[f'arm{a}_jaw' for a in ARMS]].values.max())},
    }
    meta = {
        'episode_id': session_dir.name,
        'source_session': str(session_dir),
        'source_alignment': str(align_path),
        'generated_by': 'scripts/tools/build_srth_dataset.py',
        'grid_hz': 30.0,
        'sample_count': int(n_chunks),
        'action_dim': 40,
        'chunk_steps': args.chunk_steps,
        'tail_strategy': ('drop samples whose 60-step chunk would extend past the '
                          'episode end (no padding); %d tail samples dropped' % tail_dropped),
        'tail_samples_dropped': int(tail_dropped),
        'filter': {
            'alignment_rows': int(total_rows),
            'pose_valid_rows': int(len(with_pose)),
            'all_cameras_valid_rows': int(len(with_all_cams)),
            'kept_rows': int(len(grid)),
            'filtered_out_fraction': float(filtered_frac),
            'error_threshold_filtered_fraction': float(
                1.0 - len(grid) / max(len(with_all_cams), 1)),
            'error_threshold_ms': args.error_threshold_ms,
        },
        'annotations': (annotations if annotations
                        else {'note': 'annotations.json absent at build time'}),
        'alignment_errors_ms': cam_err,
        'coordinate_notes': {
            'delta_pos_frame': 'scope frame (scope_position_*), mm',
            'delta_rot_frame': ('base-frame orientation quaternions (PLC exposes scope '
                                'positions only); for small steps the rotation angle is '
                                'frame-invariant, the axis differs by the uncalibrated '
                                'scope<->base rotation — reported limitation, not fixed'),
            'quaternion_order': 'PLC wxyz -> reordered to xyzw for scipy',
            'jaw_normalisation': {'lo': args.jaw_lo, 'hi': args.jaw_hi,
                                  'invert': args.jaw_invert,
                                  'direction_note': ('larger angle = more open (provisional '
                                                     'dVRK-style convention; gripper_signal is '
                                                     'constant 0 in all sessions, direction '
                                                     'pending live test — see T1/13.5')},
            'arm_active_mask': 'motion heuristic: |dpos|>%.2f mm or |drot|>%.1f deg per step' % (
                args.mask_pos_mm, args.mask_rot_deg),
        },
        'jaw_stats_raw': jaw_stats,
        'base_frame_poses': base_pose_stats,
        'action_ranges': action_ranges,
        'hl_policy': {'interval_s': args.hl_interval_s, 'window': args.hl_window,
                      'sample_count': int(len(hl))},
        'obs': {'streams': {s: {'frames': len(f)} for s, f in kept_files.items()},
                'images_copied': not args.no_copy,
                'manifest': manifest},
        'session_meta': (session_meta if session_meta
                         else {'note': 'session_meta.json absent at build time'}),
        'self_tests': self_tests,
    }
    (out_dir / 'meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    # ---- console summary ------------------------------------------------
    print('\n=== episode summary ===')
    print(f'  episode dir:            {out_dir}')
    print(f'  samples (chunk rows):   {n_chunks}   (tail dropped: {tail_dropped})')
    print(f'  action dim:             40 ({len(grid)} grid rows -> {len(actions)} step rows)')
    print(f'  HL samples:             {len(hl)}')
    print(f'  obs frames:             ' + ', '.join(
        f'{s}={len(f)}' for s, f in kept_files.items()))
    print(f'\n  action ranges:')
    for k, v in action_ranges.items():
        print(f'    {k}: [{v["min"]:.4f}, {v["max"]:.4f}]')
    print(f'\n  alignment errors (kept rows):')
    for c, v in cam_err.items():
        print(f'    {c}: median={v["median_ms"]:.2f} ms  p99={v["p99_ms"]:.2f} ms  '
              f'max={v["max_ms"]:.2f} ms')
    print(f'\n  arm_active_mask (fraction active per arm):')
    for a in ARMS:
        print(f'    arm{a}: {actions[f"arm{a}_active"].mean():.3f}')

    # anomaly guard: report grossly wrong action scales instead of "fixing" them
    dp = action_ranges['delta_pos_mm']
    if abs(dp['min']) > 50 or abs(dp['max']) > 50:
        print(f'\n  !! delta_pos magnitude > 50 mm/step (min={dp["min"]:.2f}, '
              f'max={dp["max"]:.2f}) — STOP and report (possible unit/frame issue).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
