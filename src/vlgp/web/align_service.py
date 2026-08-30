"""On-demand alignment generation for the web platform (T15 v0).

When a session without a camera-anchor alignment table is opened in the web
UI, ``start_alignment`` runs the same pipeline as the CLI tool
(``python -m vlgp.alignment.align_camera_anchor --session ...``) in a
background thread: fit PLC-PC drift model -> map camera timestamps -> 30 Hz
anchor grid with interpolated arm poses -> write
``camera_anchor_alignment.csv`` and ``camera_anchor_alignment_valid.csv``.

Progress is published into a per-session job record that the frontend polls
via ``GET /api/sessions/{name}/align/status`` (stages robot / drift /
cameras / prepare / grid).

The CLI defaults are kept (anchor=camera_arm_2, others=[camera_arm_1,
camera_arm_3], include_joints=False) so web-generated output is byte-identical
to the CLI's.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from vlgp.acquisition import session_layout as sl
from vlgp.alignment.align_camera_anchor import build_camera_anchor_alignment
from vlgp.web.session_index import SessionIndex

ANCHOR = 'camera_arm_2'
OTHER_CAMERAS = ('camera_arm_1', 'camera_arm_3')
INCLUDE_JOINTS = False

STAGE_LABELS = {
    'robot': '加载机器人数据',
    'drift': '拟合漂移模型',
    'cameras': '加载相机元数据',
    'prepare': '预计算插值数据',
    'grid': '生成 30Hz 锚点网格',
    'done': '对齐完成',
}

# Module-level job registry (single-process deployment, see 三.8 in
# CODE_REVIEW_20260830): grows per session, never evicted — fine for the
# shipped one-server setup.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _new_job() -> dict:
    return {'status': 'running', 'stage': 'robot', 'current': 0, 'total': 0,
            'message': STAGE_LABELS['robot'], 'started_at': time.time(),
            'elapsed_s': None, 'result': None, 'error': None}


def start_alignment(index: SessionIndex, name: str) -> dict:
    """Start (async) alignment generation for ``name`` if needed.

    Returns immediately:
      - {'status': 'already_aligned', 'n_rows': int}   table already exists
      - {'status': 'started'}                          job spawned (or already
                                                       running for this session)

    Raises ValueError with a user-facing Chinese message for missing robot
    data or missing anchor-camera metadata (checked synchronously so the
    frontend gets an immediate 422).
    """
    d = index.session_dir(name)
    out_path = sl.alignment_path(d)
    if out_path.exists():
        return {'status': 'already_aligned', 'n_rows': int(len(index.alignment(name)))}

    with _jobs_lock:
        job = _jobs.get(name)
        if job is not None and job.get('status') == 'running':
            return {'status': 'started'}

    # Synchronous preconditions (mirror CLI failure modes, clear messages).
    if not _robot_arms_available(d):
        raise ValueError(
            '无法生成对齐：会话没有机器人数据（robot_pose 缺失或为空，'
            '采集时 PLC/ADS 链路可能未就绪）')
    meta = sl.camera_metadata_path(d, ANCHOR)
    if not meta.exists():
        raise ValueError(f'无法生成对齐：锚点相机元数据缺失 ({meta})')

    with _jobs_lock:
        _jobs[name] = _new_job()
    threading.Thread(target=_run_job, args=(index, name), daemon=True).start()
    return {'status': 'started'}


def alignment_status(name: str) -> dict:
    """Current progress of the alignment job for ``name`` (or idle)."""
    with _jobs_lock:
        job = _jobs.get(name)
        if job is None:
            return {'status': 'idle', 'stage': '', 'current': 0, 'total': 0,
                    'message': ''}
        return dict(job)


def _run_job(index: SessionIndex, name: str) -> None:
    d = index.session_dir(name)

    def cb(stage: str, done: int, total: int) -> None:
        with _jobs_lock:
            job = _jobs[name]
            job['stage'] = stage
            job['current'] = int(done)
            job['total'] = int(total)
            job['message'] = STAGE_LABELS.get(stage, stage)

    try:
        t0 = time.perf_counter()
        df, stats = build_camera_anchor_alignment(
            d, ANCHOR, list(OTHER_CAMERAS), INCLUDE_JOINTS, progress_cb=cb)
        out_path = sl.alignment_path(d)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False)
        valid = df[df['pose_valid'] & df['all_cameras_valid']]
        valid.to_csv(d / sl.ALIGNMENT_VALID_REL, index=False)
        index.invalidate(name)
        with _jobs_lock:
            _jobs[name].update(
                status='done', stage='done', current=1, total=1,
                message=STAGE_LABELS['done'],
                elapsed_s=round(time.perf_counter() - t0, 2),
                result={
                    'n_rows': int(len(df)),
                    'pose_valid_rows': int(stats['pose_valid_rows']),
                    'valid_rows': int(len(valid)),
                    'drift_ms': {k: float(v) for k, v in stats['drift'].items()},
                })
    except Exception as e:  # noqa: BLE001 — surface any failure to the UI
        with _jobs_lock:
            _jobs[name].update(status='error', stage='error', message=str(e),
                               error=str(e))


def _robot_arms_available(session_dir: Path) -> bool:
    root = sl.robot_pose_root(session_dir)
    if not root.is_dir():
        return False
    csvs = list(root.glob('robot_pose_arm*/twincat_poses.csv'))
    if not csvs:
        return False
    return any(p.stat().st_size > 0 for p in csvs)
