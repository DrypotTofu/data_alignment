"""Checkerboard calibration capture + intrinsics estimation.

Flow (per camera, one at a time):
    1. Open the target camera (DSHOW) at the configured resolution.
    2. Live view with checkerboard corner detection overlay.
    3. Press SPACE to save a frame when detection quality is good
       (the frame is saved only if corners were found and the board
       occupies a reasonable area). 15-25 frames per camera are recommended.
    4. Press 'q' to finish this camera; repeat for the next camera.
    5. When all cameras are done, run intrinsics calibration and write
       ``<calib_dir>/<camera>/intrinsics.yaml`` + ``calibration.json``.

Usage:
    python -m vlgp.calibration.collect_calibration --board 9x6 --square-mm 22.5
    python -m vlgp.calibration.collect_calibration --cameras arm_2,arm_1 \
        --no-calibrate   # capture only, skip intrinsics
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

from vlgp.acquisition.naming import arm_name  # noqa: E402

# arm_id -> camera index (mirrors scripts/config/default.yaml camera_bindings)
DEFAULT_BINDINGS = {'arm_1': 1, 'arm_2': 0, 'arm_3': 3}
# arm_id -> (width, height) from default.yaml sources
DEFAULT_RESOLUTIONS = {'arm_2': (1280, 720), 'arm_1': (640, 480), 'arm_3': (640, 480)}
BACKENDS = {'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF, 'any': cv2.CAP_ANY}


def parse_board(spec: str) -> tuple[int, int]:
    """Parse 'WxH' inner-corner spec, e.g. '9x6' -> (9, 6)."""
    try:
        w, h = (int(v) for v in spec.lower().split('x'))
    except ValueError as exc:
        raise SystemExit(f'Invalid --board spec {spec!r}; expected e.g. 9x6') from exc
    if w < 2 or h < 2:
        raise SystemExit(f'Invalid board size {w}x{h}')
    return w, h


def capture_camera(
    camera_index: int,
    camera_name: str,
    out_dir: Path,
    board_size: tuple[int, int],
    width: int,
    height: int,
    backend_name: str = 'dshow',
    target_frames: int = 20,
    min_corners_area_ratio: float = 0.12,
) -> int:
    """Interactively capture checkerboard frames from one camera.

    Returns the number of frames saved.
    """
    backend = BACKENDS[backend_name]
    cap = cv2.VideoCapture(camera_index, backend)
    if not cap.isOpened():
        print(f'ERROR: cannot open camera index {camera_index} ({backend_name})')
        return 0

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

    time.sleep(0.5)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames_dir = out_dir / camera_name / 'frames'
    frames_dir.mkdir(parents=True, exist_ok=True)

    pattern_size = tuple(board_size)
    saved = 0
    window_name = f'{camera_name} (idx {camera_index}) calib - SPACE:save q:next'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    print(f'\n=== {camera_name}: {actual_w}x{actual_h} @ index {camera_index} ===')
    print(f'Target: {target_frames} frames. Press SPACE to save (auto-detect), q to finish.')

    while saved < target_frames:
        ret, frame = cap.read()
        if not ret or frame is None:
            print('Frame read failed, stopping camera')
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCorners(gray, pattern_size, None)
        draw = frame.copy()
        if found:
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            cv2.drawChessboardCorners(draw, pattern_size, corners, found)
            # coverage heuristic: corners bounding box area vs frame area
            xs = corners[:, 0, 0]
            ys = corners[:, 0, 1]
            area_ratio = (xs.max() - xs.min()) * (ys.max() - ys.min()) / (actual_w * actual_h)
        else:
            area_ratio = 0.0

        status = f'{saved}/{target_frames} saved | corners: {"YES" if found else "no"}' \
                 f' | board area: {area_ratio:.2f}'
        cv2.putText(draw, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2, cv2.LINE_AA)
        cv2.imshow(window_name, draw)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), 27):
            break
        if key == ord(' ') or key == 13:  # SPACE or Enter
            if found and area_ratio >= min_corners_area_ratio:
                ts = int(time.time_ns())
                path = frames_dir / f'{camera_name}_calib_{saved:03d}_{ts}.png'
                cv2.imwrite(str(path), frame)
                saved += 1
                print(f'  saved [{saved}] {path.name} (corners={len(corners)}, area={area_ratio:.2f})')
            else:
                print('  skip: board not detected or too small in frame')

    cap.release()
    cv2.destroyAllWindows()
    print(f'{camera_name}: done, {saved} frames -> {frames_dir}')
    return saved


def calibrate_camera_frames(frames_dir: Path, board_size: tuple[int, int],
                            square_mm: float) -> dict:
    """Estimate intrinsics from checkerboard frames in ``frames_dir``.

    Returns a dict with camera_matrix, dist_coeffs, rms, used_frames.
    """
    pattern_size = tuple(board_size)
    objp = np.zeros((pattern_size[0] * pattern_size[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:pattern_size[0], 0:pattern_size[1]].T.reshape(-1, 2)
    objp *= square_mm

    obj_points: list[np.ndarray] = []
    img_points: list[np.ndarray] = []
    image_size = None
    used_frames: list[str] = []

    for path in sorted(frames_dir.glob('*.png')):
        img = cv2.imread(str(path))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if image_size is None:
            image_size = gray.shape[::-1]
        found, corners = cv2.findChessboardCorners(gray, pattern_size, None)
        if not found:
            continue
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
        obj_points.append(objp)
        img_points.append(corners)
        used_frames.append(path.name)

    if len(obj_points) < 3:
        return {'error': f'not enough detected frames ({len(obj_points)} < 3)'}

    rms, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
        obj_points, img_points, image_size, None, None)

    return {
        'camera_matrix': camera_matrix.tolist(),
        'dist_coeffs': dist_coeffs.tolist(),
        'rms_px': float(rms),
        'image_size': list(image_size),
        'frames_used': len(used_frames),
        'frames_total': len(list(frames_dir.glob('*.png'))),
        'frames': used_frames,
        'square_mm': square_mm,
    }


def write_intrinsics_yaml(camera_dir: Path, result: dict) -> None:
    """Write intrinsics.yaml into a camera directory."""
    if 'error' in result:
        print(f'  WARNING: {camera_dir.name}: {result["error"]}')
        return
    path = camera_dir / 'intrinsics.yaml'
    payload = {
        'camera_matrix': result['camera_matrix'],
        'dist_coeffs': result['dist_coeffs'],
        'reprojection_error_rms_px': result['rms_px'],
        'image_size': result['image_size'],
        'frames_used': result['frames_used'],
        'square_mm': result['square_mm'],
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                    encoding='utf-8')
    print(f'  wrote {path} (RMS={result["rms_px"]:.3f} px, {result["frames_used"]} frames)')


def main() -> int:
    parser = argparse.ArgumentParser(description='Checkerboard calibration capture + intrinsics')
    parser.add_argument('--cameras', type=str, default='arm_2,arm_1,arm_3',
                        help='Comma-separated arm ids, e.g. arm_2,arm_1,arm_3')
    parser.add_argument('--board', type=str, default='9x6',
                        help='Checkerboard inner corners WxH, e.g. 9x6')
    parser.add_argument('--square-mm', type=float, default=22.5,
                        help='Measured checkerboard square side in mm')
    parser.add_argument('--target-frames', type=int, default=20,
                        help='Frames to capture per camera')
    parser.add_argument('--out-dir', type=str, default=None,
                        help='Output root (default: <repo>/calibrations/calib_<ts>)')
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--no-calibrate', action='store_true',
                        help='Capture only; skip intrinsics estimation')
    args = parser.parse_args()

    board_size = parse_board(args.board)
    arms = [a.strip() for a in args.cameras.split(',') if a.strip()]

    if args.out_dir:
        calib_dir = Path(args.out_dir)
    else:
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        calib_dir = REPO_ROOT / 'calibrations' / f'calib_{ts}'
    calib_dir.mkdir(parents=True, exist_ok=True)
    print(f'Calibration directory: {calib_dir}')

    summary: dict = {}
    for arm in arms:
        if arm not in DEFAULT_BINDINGS:
            print(f'WARNING: unknown arm {arm!r}, skipping')
            continue
        cam_name = arm_name(int(arm.removeprefix('arm_'))) or f'camera_{arm}'
        idx = DEFAULT_BINDINGS[arm]
        w, h = DEFAULT_RESOLUTIONS.get(arm, (640, 480))
        n = capture_camera(idx, cam_name, calib_dir, board_size, w, h,
                           backend_name=args.backend, target_frames=args.target_frames)
        summary[cam_name] = {'index': idx, 'frames': n, 'resolution': [w, h]}

    if not args.no_calibrate:
        print('\n=== Intrinsics estimation ===')
        for arm in arms:
            cam_name = arm_name(int(arm.removeprefix('arm_'))) or f'camera_{arm}'
            camera_dir = calib_dir / cam_name
            frames_dir = camera_dir / 'frames'
            if not frames_dir.exists():
                continue
            result = calibrate_camera_frames(frames_dir, board_size, args.square_mm)
            write_intrinsics_yaml(camera_dir, result)
            summary.setdefault(cam_name, {})['rms_px'] = result.get('rms_px')

    meta = {
        'calibration_id': calib_dir.name,
        'created_at_beijing': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
        'operator': '',
        'board': {'type': 'checkerboard', 'inner_corners': list(board_size),
                  'square_mm': args.square_mm},
        'camera_bindings': DEFAULT_BINDINGS,
        'cameras': summary,
        'notes': '',
    }
    (calib_dir / 'calibration.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nCalibration meta: {calib_dir / "calibration.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
