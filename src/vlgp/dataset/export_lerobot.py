"""T17: export internal SRT-H episodes to a LeRobotDataset v3.0 dataset.

Position in the pipeline (AGENT_HANDOFF T17): a thin exporter layer AFTER
``build_srth_dataset`` — internal episode (srth_episode/) is kept as the
intermediate representation; this module converts it to the LeRobot v3.0
file-based format.

It uses the OFFICIAL lerobot writing API (``LeRobotDataset.create`` /
``add_frame`` / ``save_episode`` / ``save_episode_tasks`` / ``finalize``),
so the output loads with the unmodified ``LeRobotDataset`` class. Schema was
confirmed against the installed ``lerobot`` package source (v0.4.4, the
latest on PyPI; the PM note "v0.6.x" refers to the HF docs versioning):
meta/info.json + stats.json + tasks.parquet + meta/episodes/chunk-*/file-*.parquet,
data/chunk-*/file-*.parquet (one file, multiple episodes), videos/<cam>/chunk-*/file-*.mp4.

Feature mapping (per AGENT_HANDOFF T17, decided at implementation time):
    observation.state            float32 [32]  per-arm base-frame pos3+quat4+
                                               gripper1 (4 arms); names provided
    observation.instrument_type  int64   [4]   per-arm instrument code (0..13)
    observation.active_mask      bool    [4]   per-arm arm_active_mask
    action                       float32 [40]  the SRT-H 40-dim action
                                               (4x(3 delta_pos + 6 delta_rot_6d)
                                               + 4 jaw); the active mask is kept
                                               as a separate observation feature
    observation.images.camera_arm_{2,1,3}  video  endoscope + wrist streams
    timestamp / frame_index / episode_index / task_index   (lerobot defaults)
    task                        session-level task_instruction placeholder for
                                now; T15 segment instructions replace it later

Video encoding: lerobot defaults (libsvtav1, crf 30); falls back to libx264
if svt-av1 is unavailable in PyAV's ffmpeg build. Original PNG frames stay
in the source session (PNG master remains the lossless source).

Usage::

    python -m vlgp.dataset.export_lerobot \\
        --episode sessions/20260821_123455/srth_episode \\
        --out sessions/20260821_123455/lerobot_dataset \\
        [--repeat 3] [--task "grasp the needle"] [--vcodec libx264]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from vlgp.constants import ARMS, CAMERA_STREAMS, FILENAME_COLS

logger = logging.getLogger('export_lerobot')

CAMERA_KEYS = {
    'camera_arm_2': 'observation.images.camera_arm_2',   # endoscope (anchor)
    'camera_arm_1': 'observation.images.camera_arm_1',   # wrist left
    'camera_arm_3': 'observation.images.camera_arm_3',   # wrist right
}
STATE_COLS = [f'arm{a}_{c}'
              for a in ARMS
              for c in ('position_x', 'position_y', 'position_z',
                        'orientation_w', 'orientation_x', 'orientation_y',
                        'orientation_z', 'gripper_angle')]
STATE_NAMES = [f'arm{a}_{c}'
               for a in ARMS
               for c in ('pos_x', 'pos_y', 'pos_z',
                         'qw', 'qx', 'qy', 'qz', 'gripper')]
# 40-dim SRT-H action; the same names serve as the parquet source columns
# and the LeRobot feature names (previously duplicated as ACTION_NAMES).
ACTION_COLS = ([f'arm{a}_delta_pos_{c}' for a in ARMS for c in ('x', 'y', 'z')]
               + [f'arm{a}_delta_rot_6d_{k}' for a in ARMS for k in range(6)]
               + [f'arm{a}_jaw' for a in ARMS])
ACTION_NAMES = ACTION_COLS
INSTRUMENT_COLS = [f'arm{a}_instrument_type' for a in ARMS]
ACTIVE_COLS = [f'arm{a}_active' for a in ARMS]

REPO_ROOT = Path(__file__).resolve().parents[3]


def build_features(img_sizes: dict[str, tuple[int, int]]) -> dict:
    """LeRobot features dict (video features shaped (H, W, 3))."""
    features = {
        'observation.state': {
            'dtype': 'float32', 'shape': (len(STATE_NAMES),), 'names': STATE_NAMES,
        },
        'observation.instrument_type': {
            'dtype': 'int64', 'shape': (4,), 'names': [f'arm{a}' for a in ARMS],
        },
        'observation.active_mask': {
            'dtype': 'bool', 'shape': (4,), 'names': [f'arm{a}' for a in ARMS],
        },
        'action': {
            'dtype': 'float32', 'shape': (len(ACTION_NAMES),), 'names': ACTION_NAMES,
        },
    }
    for cam, key in CAMERA_KEYS.items():
        h, w = img_sizes[cam]
        features[key] = {'dtype': 'video', 'shape': (h, w, 3),
                         'names': ['height', 'width', 'channel']}
    return features


def resolve_frame_dir(episode_dir: Path, cam: str) -> Path | None:
    """Where frames for ``cam`` live: obs/<stream>/ first, else source session."""
    stream = CAMERA_STREAMS[cam]
    obs = episode_dir / 'obs' / stream
    if obs.is_dir() and any(obs.iterdir()):
        return obs
    # fall back to the source session's camera dir (meta stores a repo-relative path)
    meta = json.loads((episode_dir / 'meta.json').read_text(encoding='utf-8'))
    cam_dir = REPO_ROOT / meta.get('source_session', '') / cam
    return cam_dir if cam_dir.is_dir() else None


def export_episode(
    dataset, episode_dir: Path, task: str, episode_index: int,
    parallel_encoding: bool = False,
) -> dict:
    """Add one internal episode to the dataset via the official API."""
    meta = json.loads((episode_dir / 'meta.json').read_text(encoding='utf-8'))
    df = pd.read_parquet(episode_dir / 'actions.parquet')

    frame_dirs = {cam: resolve_frame_dir(episode_dir, cam) for cam in CAMERA_KEYS}
    for cam, d in frame_dirs.items():
        if d is None:
            raise FileNotFoundError(f'no frame source for {cam} in {episode_dir}')

    n = len(df)
    for i, (_, row) in enumerate(df.iterrows()):
        state = np.asarray([float(row[c]) for c in STATE_COLS], dtype=np.float32)
        state = np.nan_to_num(state, nan=0.0)
        action = np.asarray([float(row[c]) for c in ACTION_COLS], dtype=np.float32)
        instr = np.asarray([int(row[c]) if row[c] == row[c] else 0
                            for c in INSTRUMENT_COLS], dtype=np.int64)
        active = np.asarray([bool(row[c]) for c in ACTIVE_COLS], dtype=bool)

        frame: dict = {
            'task': task,
            'observation.state': state,
            'observation.instrument_type': instr,
            'observation.active_mask': active,
            'action': action,
        }
        # NOTE: no 'timestamp'/'episode_index' keys here — add_frame generates
        # them internally (timestamp = frame_index / fps, the canonical 1/fps
        # time axis required by delta_timestamps). The true PLC timestamps
        # stay in the source episode (internal layer).
        import cv2
        for cam, key in CAMERA_KEYS.items():
            fname = str(row[FILENAME_COLS[cam]])
            img = cv2.imread(str(frame_dirs[cam] / fname))
            if img is None:
                raise FileNotFoundError(f'{cam}: cannot read {fname}')
            # cv2 reads BGR; lerobot's video pipeline (temp PNG + encode) is
            # RGB throughout — convert here or the exported videos have
            # swapped R/B channels (roundtrip mean|diff| ~20+ vs <5 after fix).
            frame[key] = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        dataset.add_frame(frame)
        if (i + 1) % 200 == 0 or i + 1 == n:
            logger.info(f'  episode {episode_index}: frame {i + 1}/{n}')
    dataset.save_episode(parallel_encoding=parallel_encoding)
    return {'episode_index': episode_index, 'n_frames': n,
            'task': task, 'source': str(episode_dir)}


def main() -> int:
    parser = argparse.ArgumentParser(description='T17: export episodes to LeRobotDataset v3.0')
    parser.add_argument('--episode', type=Path, nargs='+', required=True,
                        help='One or more srth_episode directories')
    parser.add_argument('--out', type=Path, required=True,
                        help='Output dataset root (must not exist)')
    parser.add_argument('--repo-id', default='vlgp/local-surgical',
                        help='Dataset repo id (local only; no hub upload)')
    parser.add_argument('--task', default='',
                        help='Task string for tasks.parquet (default: episode '
                             'language.json task_instruction, else placeholder)')
    parser.add_argument('--repeat', type=int, default=1,
                        help='Export each episode N times (multi-episode shard test)')
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--robot-type', default='VLGP-4arm-surgical')
    parser.add_argument('--vcodec', default='libsvtav1',
                        choices=['libsvtav1', 'h264', 'hevc', 'auto'],
                        help='Video codec (lerobot names; default libsvtav1 like lerobot)')
    parser.add_argument('--parallel', action='store_true',
                        help='Parallel per-camera video encoding (uses multiprocessing '
                             'pipes; disable under sandboxes that forbid named pipes)')
    parser.add_argument('--v', action='store_true', help='verbose logging')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.v else logging.INFO,
                        format='%(message)s')

    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    if args.out.exists():
        logger.error(f'output root already exists: {args.out} (refusing to overwrite)')
        return 2

    # Determine camera image sizes from the first episode's first frames.
    img_sizes: dict[str, tuple[int, int]] = {}
    ep0 = args.episode[0]
    import cv2
    for cam in CAMERA_KEYS:
        fdir = resolve_frame_dir(ep0, cam)
        df0 = pd.read_parquet(ep0 / 'actions.parquet')
        fname = str(df0.iloc[0][FILENAME_COLS[cam]])
        h, w = cv2.imread(str(fdir / fname)).shape[:2]
        img_sizes[cam] = (h, w)

    features = build_features(img_sizes)
    dataset = LeRobotDataset.create(
        args.repo_id, fps=args.fps, features=features,
        robot_type=args.robot_type, root=args.out, use_videos=True,
        vcodec=args.vcodec,
    )

    all_tasks: list[str] = []
    ep_idx = 0
    for ep_dir in args.episode:
        lang = json.loads((ep_dir / 'language.json').read_text(encoding='utf-8'))
        task = args.task or lang.get('task_instruction') or f'{ep_dir.parent.name}'
        for _ in range(max(1, args.repeat)):
            logger.info(f'exporting {ep_dir} as episode {ep_idx} (task={task!r})')
            export_episode(dataset, ep_dir, task, ep_idx,
                           parallel_encoding=args.parallel)
            all_tasks.append(task)
            ep_idx += 1

    dataset.meta.save_episode_tasks(sorted(set(all_tasks)))
    dataset.finalize()
    logger.info(f'exported {ep_idx} episode(s) -> {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
