# -*- coding: utf-8 -*-
"""T17 round-trip validation: export -> load with UNMODIFIED LeRobotDataset.

Checks (AGENT_HANDOFF T17 acceptance):
  1. the exported dataset loads with the official LeRobotDataset, zero errors;
  2. frame counts / timestamps / per-field values match the source episode;
  3. camera frames decoded from the exported MP4s match the source PNGs
     (lossy-video tolerance);
  4. delta_timestamps window sampling works;
  5. multi-episode sharding (>=3 episodes) with correct episodes metadata.

Usage:
    python scripts/test/verification/lerobot_roundtrip.py --dataset <root> --episode <srth_episode_dir> [--n-frames 20]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# HF datasets/hub caches must live inside the workspace (the DSH sandbox
# blocks writes to %USERPROFILE%\.cache under workspace-write mode).
_CACHE = Path(__file__).resolve().parents[3] / 'var' / 'hf_cache'
os.environ.setdefault('HF_DATASETS_CACHE', str(_CACHE / 'datasets'))
os.environ.setdefault('HF_HOME', str(_CACHE))
os.environ.setdefault('HF_HUB_CACHE', str(_CACHE / 'hub'))
_CACHE.mkdir(parents=True, exist_ok=True)

from vlgp.dataset.export_lerobot import (  # noqa: E402
    ACTION_COLS, CAMERA_KEYS, FILENAME_COLS, STATE_COLS,
)

PASS, FAIL = 'PASS', 'FAIL'
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    _results.append((name, PASS if ok else FAIL, detail))
    print(f'  [{PASS if ok else FAIL}] {name}' + (f' — {detail}' if detail else ''))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--episode', type=Path, required=True)
    parser.add_argument('--n-frames', type=int, default=20,
                        help='number of frames to compare per camera')
    args = parser.parse_args()

    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    import warnings

    # ---- 1. load with unmodified official class, count warnings/errors
    print('=== 1. load with official LeRobotDataset ===')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        ds = LeRobotDataset(repo_id='vlgp/local-surgical', root=args.dataset,
                            download_videos=False)
        warn_msgs = [str(w.message) for w in caught]
    check('loads without warnings', len(warn_msgs) == 0, f'{len(warn_msgs)} warning(s): {warn_msgs[:3]}')
    check('dataset length == source frames', len(ds) == len(pd.read_parquet(args.episode / 'actions.parquet')) * _ep_count(ds),
          f'len(ds)={len(ds)}')

    # ---- 2. per-field numeric round-trip vs source actions.parquet
    print('\n=== 2. numeric round-trip ===')
    src = pd.read_parquet(args.episode / 'actions.parquet')
    state_ok = action_ok = ts_ok = True
    max_state = max_action = 0.0
    for i in range(min(args.n_frames, len(src))):
        item = ds[i]
        st = np.asarray(item['observation.state'])
        ac = np.asarray(item['action'])
        exp_state = np.asarray([float(src.iloc[i][c]) for c in STATE_COLS], dtype=np.float32)
        exp_state = np.nan_to_num(exp_state, nan=0.0)
        exp_action = np.asarray([float(src.iloc[i][c]) for c in ACTION_COLS], dtype=np.float32)
        max_state = max(max_state, float(np.abs(st - exp_state).max()))
        max_action = max(max_action, float(np.abs(ac - exp_action).max()))
        if not np.allclose(st, exp_state, atol=1e-4): state_ok = False
        if not np.allclose(ac, exp_action, atol=1e-4): action_ok = False
    check('observation.state round-trip', state_ok, f'max|diff|={max_state:.2e}')
    check('action round-trip', action_ok, f'max|diff|={max_action:.2e}')
    ts = np.asarray([ds[i]['timestamp'] for i in range(min(args.n_frames, len(src)))])
    exp_ts = (np.arange(len(ts)) / 30.0).astype(np.float64)  # canonical frame_index/fps axis
    check('timestamps on 1/fps axis', np.allclose(ts, exp_ts, atol=1e-4),
          f'max|diff|={np.abs(ts - exp_ts).max():.2e} s')

    # ---- 3. image round-trip (lossy tolerance)
    print('\n=== 3. image round-trip (MP4 decode vs source PNG) ===')
    import importlib
    m = importlib.import_module('vlgp.dataset.export_lerobot')
    for cam, key in CAMERA_KEYS.items():
        frame_dir = m.resolve_frame_dir(args.episode, cam)
        worst = 0.0
        ok = True
        detail = ''
        for i in range(min(args.n_frames, len(src))):
            img_ds = np.asarray(ds[i][key])
            fname = str(src.iloc[i][FILENAME_COLS[cam]])
            img_src = cv2.imread(str(frame_dir / fname))
            if img_src is None:
                ok = False
                detail = f'frame {i}: source PNG unreadable: {frame_dir / fname}'
                break
            # decode_video_frames_torchvision returns float32 CHW in [0, 1];
            # normalize to uint8 HWC to compare with cv2.imread (uint8 HWC BGR).
            if img_ds.ndim == 3 and img_ds.shape[-1] not in (3, 4) and img_ds.shape[0] in (1, 3, 4):
                img_ds = np.transpose(img_ds, (1, 2, 0))
            if img_ds.dtype != np.uint8:
                img_ds = (np.clip(img_ds, 0.0, 1.0) * 255.0).astype(np.uint8)
            if img_src.shape[:2] != img_ds.shape[:2]:
                ok = False
                detail = f'frame {i}: shape src={img_src.shape} ds={img_ds.shape}'
                break
            if img_ds.shape[2] == 3:  # decoder yields RGB; source PNG is BGR
                img_ds = cv2.cvtColor(img_ds, cv2.COLOR_RGB2BGR)
            diff = np.abs(img_src.astype(np.float32) - img_ds.astype(np.float32)).mean()
            worst = max(worst, float(diff))
        if ok:
            ok = worst < 12.0
            detail = f'worst mean|diff|={worst:.2f}'
        check(f'images {cam} match (mean|diff|<12)', ok, detail)

    # ---- 4. delta_timestamps window sampling smoke
    print('\n=== 4. delta_timestamps sampling ===')
    dt = {
        'observation.state': [0.0, 0.1],
        'action': [0.0, 0.1],
        'observation.images.camera_arm_2': [0.0],
    }
    try:
        ds_dt = LeRobotDataset(repo_id='vlgp/local-surgical', root=args.dataset,
                               download_videos=False, delta_timestamps=dt)
        sample = ds_dt[0]
        ok = (np.asarray(sample['observation.state']).shape[0] == 2
              and np.asarray(sample['action']).shape[0] == 2)
        check('delta_timestamps window sample', ok,
              f"state window={np.asarray(sample['observation.state']).shape}")
    except Exception as e:  # noqa: BLE001
        check('delta_timestamps window sample', False, f'exception: {e}')

    # ---- 5. multi-episode metadata
    print('\n=== 5. episodes metadata (multi-episode sharding) ===')
    n_ep = _ep_count(ds)
    check('episodes count >= 3', n_ep >= 3, f'{n_ep} episodes')
    lens = ds.meta.episodes['length'].tolist() if hasattr(ds.meta.episodes, 'columns') else []
    check('per-episode length consistent', len(set(lens)) <= 1,
          f'lengths={lens}')
    total = int(ds.meta.total_frames)
    check('total_frames == sum lengths', total == sum(lens) if lens else True,
          f'total_frames={total}')

    n_pass = sum(1 for _, st, _ in _results if st == PASS)
    print(f'\n===== roundtrip: {n_pass}/{len(_results)} PASS =====')
    for name, st, detail in _results:
        if st == FAIL:
            print(f'  FAILED: {name} {detail}')
    return 0 if n_pass == len(_results) else 1


def _ep_count(ds) -> int:
    eps = getattr(ds.meta, 'episodes', None)
    if eps is None:
        return 0
    return len(eps)


if __name__ == '__main__':
    sys.exit(main())
