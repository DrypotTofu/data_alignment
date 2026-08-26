"""On-demand alignment generation for the web platform (T15 v0).

When a session without a camera-anchor alignment table is opened in the web
UI, ``ensure_alignment`` runs the same pipeline as the CLI tool
(``python -m vlgp.alignment.align_camera_anchor --session ...``):
fit PLC-PC drift model -> map camera timestamps -> 30 Hz anchor grid with
interpolated arm poses -> write ``camera_anchor_alignment.csv`` and
``camera_anchor_alignment_valid.csv``.

The CLI defaults are kept (anchor=camera_arm_2, others=[camera_arm_1,
camera_arm_3], include_joints=False) so web-generated output is byte-identical
to the CLI's.
"""
from __future__ import annotations

import time
from pathlib import Path

from vlgp.alignment.align_camera_anchor import build_camera_anchor_alignment
from vlgp.acquisition import session_layout as sl
from vlgp.web.session_index import SessionIndex

ANCHOR = 'camera_arm_2'
OTHER_CAMERAS = ('camera_arm_1', 'camera_arm_3')
INCLUDE_JOINTS = False


def ensure_alignment(index: SessionIndex, name: str) -> dict:
    """Generate the alignment table for ``name`` if it does not exist yet.

    Returns a summary dict:
      - {'status': 'already_aligned', 'n_rows': int}   when the table exists
      - {'status': 'generated', 'n_rows': int, 'pose_valid_rows': int,
         'drift_ms': {...}, 'elapsed_s': float}        after generation

    Raises ValueError with a user-facing Chinese message for missing robot
    data or missing anchor-camera metadata.
    """
    d = index.session_dir(name)
    out_path = sl.alignment_path(d)
    if out_path.exists():
        df = index.alignment(name)
        return {'status': 'already_aligned', 'n_rows': int(len(df))}

    # Preconditions mirror the CLI failure modes, but with clear messages.
    arms = _robot_arms_available(d)
    if not arms:
        raise ValueError(
            '无法生成对齐：会话没有机器人数据（robot_pose 缺失或为空，'
            '采集时 PLC/ADS 链路可能未就绪）')
    meta = sl.camera_metadata_path(d, ANCHOR)
    if not meta.exists():
        raise ValueError(f'无法生成对齐：锚点相机元数据缺失 ({meta})')

    t0 = time.perf_counter()
    df, stats = build_camera_anchor_alignment(
        d, ANCHOR, list(OTHER_CAMERAS), INCLUDE_JOINTS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    valid = df[df['pose_valid'] & df['all_cameras_valid']]
    valid.to_csv(d / sl.ALIGNMENT_VALID_REL, index=False)
    elapsed = time.perf_counter() - t0

    index.invalidate(name)
    return {
        'status': 'generated',
        'n_rows': int(len(df)),
        'pose_valid_rows': int(stats['pose_valid_rows']),
        'valid_rows': int(len(valid)),
        'drift_ms': {k: float(v) for k, v in stats['drift'].items()},
        'elapsed_s': round(elapsed, 2),
    }


def _robot_arms_available(session_dir: Path) -> bool:
    root = sl.robot_pose_root(session_dir)
    if not root.is_dir():
        return False
    csvs = list(root.glob('robot_pose_arm*/twincat_poses.csv'))
    if not csvs:
        return False
    return any(p.stat().st_size > 0 for p in csvs)
