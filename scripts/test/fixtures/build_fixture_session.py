# -*- coding: utf-8 -*-
"""Build a synthetic 3-camera session fixture (for tests that need one).

Every session currently on disk is a single-camera test recording, so the
3-camera paths (per-camera IN/OUT export mapping, wrist frames, 3-camera
preload) had no test data at all. This fixture is deterministic and tiny:

    var/test_fixtures/synth_3cam/synth_3cam_0001/
      camera_arm_2/  camera_arm_1/  camera_arm_3/     (PNG + metadata.csv)
      camera_anchor_alignment/camera_anchor_alignment.csv
      robot_pose/robot_pose_arm{1..4}/twincat_poses.csv

Timing model (30.21 Hz anchor, 150 anchor frames = 4.96 s):
  camera_arm_2 = anchor,   starts at T0,        150 frames
  camera_arm_1 = starts 500 ms BEFORE the anchor, 200 frames
  camera_arm_3 = starts 250 ms AFTER the anchor,  160 frames

Different start offsets are what makes the IN/OUT mapping testable: the export
answers are seconds relative to EACH camera's own first frame, so with equal
offsets all three cameras would report identical numbers and a grid-relative
fallback (row/30) would look "correct". With these offsets the exact mapping
yields clearly different values per camera (offset_frames: arm2 0, arm1 +15,
arm3 -8).

Row i of the alignment table is anchor frame i and points at the nearest frame
of the other cameras; the per-arm pose columns are constant except arm2
scope_position_x which moves 1 mm per row (drives the motion curve / |dpos|).

Usage:
    python scripts/test/fixtures/build_fixture_session.py [--frames 150] [--root DIR]
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO / 'var' / 'test_fixtures' / 'synth_3cam'
SESSION = 'synth_3cam_0001'
T0_NS = 1_700_000_000_000_000_000          # arbitrary host-epoch base
STEP_NS = 33_100_000                        # 30.21 fps interval (all cameras)
# per-camera (start offset vs anchor, frame count, image size, colour)
CAM_SPEC = {
    'camera_arm_2': (0, 0, (64, 48), (40, 40, 200)),
    'camera_arm_1': (-500_000_000, 50, (32, 24), (40, 200, 40)),
    'camera_arm_3': (250_000_000, 10, (32, 24), (200, 40, 40)),
}
ARMS = (1, 2, 3, 4)


def frame_times(cam: str, frames: int) -> list[int]:
    """Absolute timestamps of one camera's frames."""
    off = CAM_SPEC[cam][0]
    return [T0_NS + off + i * STEP_NS for i in range(frames)]


def nearest_idx(times: list[int], t: int) -> int:
    import bisect
    j = bisect.bisect_left(times, t)
    if j <= 0:
        return 0
    if j >= len(times):
        return len(times) - 1
    return j if abs(times[j] - t) < abs(times[j - 1] - t) else j - 1


def build(root: Path, frames: int) -> Path:
    sess = root / SESSION
    if sess.exists():
        import shutil
        shutil.rmtree(sess)
    sess.mkdir(parents=True)

    times = {cam: frame_times(cam, frames + CAM_SPEC[cam][1]) for cam in CAM_SPEC}
    fnames = {cam: [f'{cam}_{i:08d}.png' for i in range(len(times[cam]))]
              for cam in CAM_SPEC}

    # ------------------------------------------------------------- cameras
    for cam, (_off, extra, (w, h), color) in CAM_SPEC.items():
        d = sess / cam
        d.mkdir()
        rows = []
        n = len(times[cam])
        for i in range(n):
            img = np.full((h, w, 3), color, np.uint8)
            # a marker that moves per frame (exercises real image decoding)
            x = int((i / max(1, n - 1)) * (w - 4))
            img[h // 2:h // 2 + 3, x:x + 4] = (255, 255, 255)
            cv2.imwrite(str(d / fnames[cam][i]), img)
            ts = times[cam][i]
            rows.append({'index': i, 'timestamp_ns': ts,
                         'pc_arrival_ns': ts + 1_000_000,
                         'timestamp_beijing': '', 'filename': fnames[cam][i],
                         'width': w, 'height': h})
        with open(d / 'metadata.csv', 'w', encoding='utf-8', newline='') as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)

    # --------------------------------------------------- alignment table
    anchor_times = times['camera_arm_2']
    out_rows = []
    for i in range(frames):
        t_anchor = anchor_times[i]
        row = {
            'anchor_frame_idx': i,
            'anchor_filename': fnames['camera_arm_2'][i],
            'anchor_timestamp_ns_pc': t_anchor,
            'anchor_timestamp_ns_plc': t_anchor,
            'pose_valid': True,
            'all_cameras_valid': True,
        }
        for cam in ('camera_arm_1', 'camera_arm_3'):
            j = nearest_idx(times[cam], t_anchor)
            row[f'{cam}_frame_idx'] = j
            row[f'{cam}_filename'] = fnames[cam][j]
            row[f'{cam}_error_ms'] = abs(times[cam][j] - t_anchor) / 1e6
            row[f'{cam}_in_range'] = True
        for arm in ARMS:
            row[f'arm{arm}_position_x'] = 100.0 + arm
            row[f'arm{arm}_position_y'] = 200.0 + arm
            row[f'arm{arm}_position_z'] = 300.0 + arm
            scope_x = float(i) if arm == 2 else 0.0
            row[f'arm{arm}_scope_position_x'] = scope_x
            row[f'arm{arm}_scope_position_y'] = 0.0
            row[f'arm{arm}_scope_position_z'] = 0.0
            row[f'arm{arm}_orientation_w'] = 1.0
            row[f'arm{arm}_orientation_x'] = 0.0
            row[f'arm{arm}_orientation_y'] = 0.0
            row[f'arm{arm}_orientation_z'] = 0.0
            row[f'arm{arm}_gripper_angle'] = 10.0 + arm
            row[f'arm{arm}_gripper_signal'] = 0
            row[f'arm{arm}_instrument_type'] = 13 if arm == 2 else 4
            row[f'arm{arm}_energy_instrument_status'] = 0
        out_rows.append(row)
    align_dir = sess / 'camera_anchor_alignment'
    align_dir.mkdir()
    with open(align_dir / 'camera_anchor_alignment.csv', 'w',
              encoding='utf-8', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        wr.writeheader()
        wr.writerows(out_rows)

    # ------------------------------------------------------- robot poses
    # enough for verify_alignment / row-status code paths that read them
    for arm in ARMS:
        d = sess / 'robot_pose' / f'robot_pose_arm{arm}'
        d.mkdir(parents=True)
        with open(d / 'twincat_poses.csv', 'w', encoding='utf-8',
                  newline='') as f:
            cols = ['timestamp_ns', 'timestamp_ns_pc', 'arm',
                    'position_x', 'position_y', 'position_z',
                    'scope_position_x', 'scope_position_y', 'scope_position_z',
                    'orientation_w', 'orientation_x', 'orientation_y',
                    'orientation_z', 'gripper_angle', 'instrument_type']
            wr = csv.DictWriter(f, fieldnames=cols)
            wr.writeheader()
            for ts in anchor_times:
                wr.writerow({'timestamp_ns': ts, 'timestamp_ns_pc': ts,
                             'arm': arm, 'position_x': 100.0 + arm,
                             'position_y': 200.0 + arm, 'position_z': 300.0 + arm,
                             'scope_position_x': 0.0,
                             'scope_position_y': 0.0, 'scope_position_z': 0.0,
                             'orientation_w': 1.0, 'orientation_x': 0.0,
                             'orientation_y': 0.0, 'orientation_z': 0.0,
                             'gripper_angle': 10.0 + arm,
                             'instrument_type': 13 if arm == 2 else 4})
    return sess


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames', type=int, default=150)
    ap.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    sess = build(args.root, args.frames)
    print(f'fixture root : {args.root}')
    print(f'session      : {sess}')
    print(f'anchor frames: {args.frames} @ {STEP_NS / 1e6:.2f} ms '
          f'({args.frames * STEP_NS / 1e9:.2f} s)')
    for cam in CAM_SPEC:
        off, extra, _size, _c = CAM_SPEC[cam]
        print(f'  {cam}: start {off / 1e6:+.0f} ms vs anchor, '
              f'{args.frames + extra} frames')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

