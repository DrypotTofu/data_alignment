"""Interactive camera-to-arm assignment setup.

Scans connected USB cameras, saves a preview frame for each, asks you which
arm each camera belongs to, and updates the acquisition YAML config.

Usage:
    python tools/camera_assignment_setup.py
    python tools/camera_assignment_setup.py --config config/end_to_end_1min_test.yaml
    python tools/camera_assignment_setup.py --indices 0 1 2 3
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import yaml


COMMON_RESOLUTIONS = [
    (1920, 1080),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 480),
    (320, 240),
]

BACKENDS = {
    'dshow': cv2.CAP_DSHOW,
    'msmf': cv2.CAP_MSMF,
    'any': cv2.CAP_ANY,
}

DEFAULT_ARM_RESOLUTIONS = {
    1: (640, 480),
    2: (1280, 720),
    3: (640, 480),
}


class CameraInfo:
    def __init__(self, index: int, backend_name: str = 'dshow'):
        self.index = index
        self.backend_name = backend_name
        self.available = False
        self.default_width = 0
        self.default_height = 0
        self.default_fps = 0.0
        self.resolutions: list[dict] = []
        self.preview_path: Path | None = None

    def probe(self) -> bool:
        backend = BACKENDS.get(self.backend_name, cv2.CAP_DSHOW)
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            return False

        self.available = True
        time.sleep(0.3)
        self.default_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.default_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.default_fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()

        for w, h in COMMON_RESOLUTIONS:
            cap = cv2.VideoCapture(self.index, backend)
            if not cap.isOpened():
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            time.sleep(0.2)
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            ret, frame = cap.read()
            cap.release()
            self.resolutions.append({
                'requested': (w, h),
                'actual': (actual_w, actual_h),
                'fps': actual_fps,
                'frame_read': ret and frame is not None and frame.size > 0,
            })
        return True

    def save_preview(self, out_dir: Path) -> Path | None:
        if not self.available:
            return None
        backend = BACKENDS.get(self.backend_name, cv2.CAP_DSHOW)
        # Use the highest working resolution, or default if none reported cleanly.
        working = [r for r in self.resolutions if r['frame_read']]
        if working:
            best = max(working, key=lambda r: r['actual'][0] * r['actual'][1])
            w, h = best['requested']
        else:
            w, h = self.default_width, self.default_height

        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        time.sleep(0.5)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        frame = None
        for _ in range(15):
            ret, f = cap.read()
            if ret and f is not None and f.size > 0:
                frame = f
                break
            time.sleep(0.05)
        cap.release()

        if frame is None:
            return None

        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = f'cam{self.index}_{self.backend_name}_{actual_w}x{actual_h}_{timestamp}.png'
        self.preview_path = out_dir / filename
        cv2.imwrite(str(self.preview_path), frame)
        return self.preview_path


def probe_cameras(indices: list[int], backend_name: str, out_dir: Path) -> list[CameraInfo]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cameras: list[CameraInfo] = []
    print('Scanning cameras...')
    for idx in indices:
        cam = CameraInfo(idx, backend_name)
        if cam.probe():
            cam.save_preview(out_dir)
        cameras.append(cam)
        status = 'OK' if cam.available else 'not available'
        print(f'  index {idx}: {status}')
    return cameras


def print_summary(cameras: list[CameraInfo]):
    print('\n' + '=' * 80)
    print('Detected cameras')
    print('=' * 80)
    for cam in cameras:
        if not cam.available:
            print(f'  [{cam.index}] NOT AVAILABLE')
            continue
        print(f'\n  [{cam.index}] AVAILABLE')
        print(f'      Default: {cam.default_width}x{cam.default_height} @ {cam.default_fps:.2f} fps')
        print('      Tested resolutions:')
        for r in cam.resolutions:
            rw, rh = r['requested']
            aw, ah = r['actual']
            status = 'OK' if r['frame_read'] and (aw, ah) == (rw, rh) else 'PARTIAL' if r['frame_read'] else 'FAIL'
            print(f'        {rw}x{rh} -> {aw}x{ah} @ {r["fps"]:.2f} fps [{status}]')
        if cam.preview_path:
            print(f'      Preview: {cam.preview_path}')
    print('\n' + '=' * 80)


def interactive_assign(cameras: list[CameraInfo]) -> dict[int, int]:
    """Return mapping {camera_index: arm_id}."""
    available = [c for c in cameras if c.available]
    if not available:
        print('No cameras available, nothing to assign.')
        return {}

    print('\nAssign each camera to an arm.')
    print('Recommended: arm_2 = endoscope (highest res), arm_1/3 = wrist cameras.')
    print('Enter 1, 2, 3, or s/skip to leave unassigned.')

    assignment: dict[int, int] = {}
    used_arms: set[int] = set()

    for cam in available:
        while True:
            prompt = f'  Camera {cam.index} -> arm (1/2/3/s)? '
            choice = input(prompt).strip().lower()
            if choice in ('s', 'skip', ''):
                break
            if choice not in ('1', '2', '3'):
                print('    Invalid input, please enter 1, 2, 3, or s.')
                continue
            arm_id = int(choice)
            if arm_id in used_arms:
                current_cam = next(idx for idx, aid in assignment.items() if aid == arm_id)
                print(f'    Arm {arm_id} is already assigned to camera {current_cam}.')
                print(f'    Reassigning arm {arm_id} to camera {cam.index}; camera {current_cam} will be unassigned.')
                del assignment[current_cam]
            assignment[cam.index] = arm_id
            used_arms.add(arm_id)
            break

    return assignment


def update_config(config_path: Path, assignment: dict[int, int]):
    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    sources = data.get('sources', [])
    # Backup original config (once per run) to preserve comments/layout.
    backup_path = config_path.with_suffix(config_path.suffix + '.bak')
    if not backup_path.exists():
        import shutil
        shutil.copy2(config_path, backup_path)
        print(f'Backup saved: {backup_path}')

    # Find or create camera sources for arm 1/2/3.
    camera_sources: dict[int, dict] = {}
    for src in sources:
        arm_id = src.get('arm_id')
        if arm_id in (1, 2, 3):
            camera_sources[arm_id] = src

    # Ensure each arm has a source entry.
    for arm_id in (1, 2, 3):
        if arm_id not in camera_sources:
            new_src = {
                'type': 'process_camera',
                'name': f'camera_arm_{arm_id}',
                'topic_name': f'camera/arm_{arm_id}/image_raw',
                'arm_id': arm_id,
                'fps': 30,
                'backend': 'dshow',
                'image_format': 'png',
                'ready_timeout': 20.0,
            }
            sources.append(new_src)
            camera_sources[arm_id] = new_src

    # Defaults per arm.
    defaults = {
        1: {'width': 640, 'height': 480, 'fourcc': 'MJPG', 'image_format': 'png'},
        2: {'width': 1280, 'height': 720, 'fourcc': 'MJPG', 'image_format': 'jpg'},
        3: {'width': 640, 'height': 480, 'fourcc': 'YUY2', 'image_format': 'png'},
    }

    for arm_id, src in camera_sources.items():
        if arm_id in assignment:
            cam_index = assignment[arm_id]
            src['camera_index'] = cam_index
            src['type'] = 'process_camera'
            src['name'] = f'camera_arm_{arm_id}'
            src['topic_name'] = f'camera/arm_{arm_id}/image_raw'
            src['arm_id'] = arm_id
            src['fps'] = 30
            src['backend'] = 'dshow'
            src['ready_timeout'] = 20.0
            src.update(defaults[arm_id])
        else:
            # Mark unassigned sources as disabled so they don't break the run.
            src['enabled'] = False

    data['sources'] = sources
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, sort_keys=False, allow_unicode=True)
    print(f'\nUpdated config: {config_path}')


def main():
    parser = argparse.ArgumentParser(description='Interactive camera-to-arm assignment')
    parser.add_argument('--config', type=str,
                        default=str(Path(__file__).parents[1] / 'config' / 'end_to_end_1min_test.yaml'),
                        help='Path to YAML config to update')
    parser.add_argument('--indices', type=int, nargs='+', default=list(range(10)),
                        help='Camera indices to probe (default: 0-9)')
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--out-dir', type=str, default='./camera_test_frames',
                        help='Directory for preview frames')
    args = parser.parse_args()

    config_path = Path(args.config)
    out_dir = Path(args.out_dir)

    cameras = probe_cameras(args.indices, args.backend, out_dir)
    print_summary(cameras)

    assignment = interactive_assign(cameras)
    if not assignment:
        print('No assignments made, config not changed.')
        return 0

    print('\nAssignments:')
    for arm_id, cam_index in sorted(assignment.items()):
        print(f'  arm_{arm_id} <- camera index {cam_index}')

    update_config(config_path, assignment)
    print('\nYou can now run:')
    print(f'  python run_acquisition.py --config {config_path} --duration 60')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
