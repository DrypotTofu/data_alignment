"""Interactive camera-to-arm assignment and parameter setup.

Scans connected USB cameras, benchmarks real frame rates at common
resolutions/formats, and lets you choose which camera and recording parameters
each arm should use.

Usage:
    python -m vlgp.capture.camera_assignment_setup
    python -m vlgp.capture.camera_assignment_setup --config config/default.yaml
    python -m vlgp.capture.camera_assignment_setup --indices 0 1 2 3
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import cv2
import yaml

from vlgp.capture.camera_probe import (
    BACKENDS,
    COMMON_RESOLUTIONS,
    grab_frame,
    open_camera,
    probe_default_state,
)

FOURCC_OPTIONS = ['MJPG', 'YUY2']
IMAGE_FORMAT_OPTIONS = ['jpg', 'png']

DEFAULT_ARM_SUGGESTIONS = {
    1: {'width': 640, 'height': 480, 'target_fps': 30, 'fourcc': 'MJPG', 'image_format': 'png'},
    2: {'width': 1280, 'height': 720, 'target_fps': 30, 'fourcc': 'MJPG', 'image_format': 'png'},
    3: {'width': 640, 'height': 480, 'target_fps': 30, 'fourcc': 'MJPG', 'image_format': 'png'},
}


class ResolutionProfile:
    """One (resolution, fourcc) profile with a measured real FPS."""

    def __init__(self, requested_wh: tuple[int, int], actual_wh: tuple[int, int],
                 fourcc: str, real_fps: float, frame_read: bool):
        self.requested_wh = requested_wh
        self.actual_wh = actual_wh
        self.fourcc = fourcc
        self.real_fps = real_fps
        self.frame_read = frame_read


class CameraInfo:
    def __init__(self, index: int, backend_name: str = 'dshow'):
        self.index = index
        self.backend_name = backend_name
        self.available = False
        self.default_width = 0
        self.default_height = 0
        self.default_fps = 0.0
        self.profiles: list[ResolutionProfile] = []
        self.preview_path: Path | None = None

    def probe(self, benchmark_duration_s: float = 2.0) -> bool:
        cap = open_camera(self.index, self.backend_name)
        if cap is None:
            return False

        self.available = True
        try:
            self.default_width, self.default_height, self.default_fps = \
                probe_default_state(cap)
        finally:
            cap.release()

        backend = BACKENDS.get(self.backend_name, cv2.CAP_DSHOW)
        for w, h in COMMON_RESOLUTIONS:
            for fourcc in FOURCC_OPTIONS:
                profile = self._benchmark_profile(backend, w, h, fourcc, benchmark_duration_s)
                self.profiles.append(profile)
        return True

    def _benchmark_profile(
        self,
        backend: int,
        width: int,
        height: int,
        fourcc: str,
        duration_s: float,
    ) -> ResolutionProfile:
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            return ResolutionProfile(
                (width, height), (0, 0), fourcc, 0.0, False
            )

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        time.sleep(0.3)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Read a few frames to settle and check the mode is actually working.
        frame_read = False
        for _ in range(10):
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                frame_read = True
                break
            time.sleep(0.05)

        if not frame_read:
            cap.release()
            return ResolutionProfile((width, height), (actual_w, actual_h), fourcc, 0.0, False)

        # Benchmark real FPS by reading as fast as possible for duration_s.
        frames = 0
        start_ns = time.time_ns()
        end_ns = start_ns + int(duration_s * 1e9)
        while time.time_ns() < end_ns:
            ret, _ = cap.read()
            if ret:
                frames += 1
        elapsed_s = (time.time_ns() - start_ns) / 1e9
        real_fps = frames / elapsed_s if elapsed_s > 0 else 0.0
        cap.release()

        return ResolutionProfile(
            (width, height), (actual_w, actual_h), fourcc, real_fps, True
        )

    def save_preview(self, out_dir: Path) -> Path | None:
        if not self.available:
            return None

        # Pick the highest-resolution working profile for the preview.
        working = [p for p in self.profiles if p.frame_read]
        if working:
            best = max(working, key=lambda p: p.actual_wh[0] * p.actual_wh[1])
            w, h, fourcc = best.requested_wh[0], best.requested_wh[1], best.fourcc
        else:
            w, h, fourcc = self.default_width, self.default_height, 'MJPG'

        frame = grab_frame(self.index, w, h, self.backend_name, fourcc=fourcc)
        if frame is None:
            return None

        actual_h, actual_w = frame.shape[:2]
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = f'cam{self.index}_{self.backend_name}_{actual_w}x{actual_h}_{timestamp}.png'
        out_dir.mkdir(parents=True, exist_ok=True)
        self.preview_path = out_dir / filename
        cv2.imwrite(str(self.preview_path), frame)
        return self.preview_path

    def list_working_profiles(self) -> list[ResolutionProfile]:
        return [p for p in self.profiles if p.frame_read and p.real_fps > 0]


def probe_cameras(indices: list[int], backend_name: str, out_dir: Path,
                  benchmark_duration_s: float = 2.0) -> list[CameraInfo]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cameras: list[CameraInfo] = []
    print('正在探测摄像头并测试真实帧率，请稍候...')
    for idx in indices:
        cam = CameraInfo(idx, backend_name)
        if cam.probe(benchmark_duration_s=benchmark_duration_s):
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
            print(f'\n  [{cam.index}] NOT AVAILABLE')
            continue
        print(f'\n  [{cam.index}] AVAILABLE')
        print(f'      Default: {cam.default_width}x{cam.default_height} @ {cam.default_fps:.2f} fps')
        print('      Benchmarked profiles (request -> actual, format, real fps):')
        for p in cam.profiles:
            rw, rh = p.requested_wh
            aw, ah = p.actual_wh
            status = 'OK' if p.frame_read else 'FAIL'
            print(f'        {rw}x{rh} -> {aw}x{ah}  {p.fourcc}  {p.real_fps:6.2f} fps  [{status}]')
        if cam.preview_path:
            print(f'      Preview: {cam.preview_path}')
    print('\n' + '=' * 80)


def _input_int(prompt: str, default: int | None = None,
               valid: set[int] | None = None) -> int:
    while True:
        full = prompt if default is None else f'{prompt} [{default}]: '
        val = input(full).strip()
        if val == '' and default is not None:
            return default
        try:
            num = int(val)
        except ValueError:
            print('    请输入整数。')
            continue
        if valid is not None and num not in valid:
            print(f'    请输入以下选项之一: {sorted(valid)}')
            continue
        return num


def _input_choice(prompt: str, options: list[str], default: str | None = None) -> str:
    opt_str = '/'.join(options)
    while True:
        full = prompt if default is None else f'{prompt} [{default}]: '
        val = input(f'{full} ({opt_str}) ').strip()
        if val == '' and default is not None:
            return default
        if val in options:
            return val
        print(f'    请输入以下选项之一: {options}')


def _pick_profile_for_camera(cam: CameraInfo) -> ResolutionProfile:
    """Let the user pick a resolution/format profile for one camera."""
    working = cam.list_working_profiles()
    if not working:
        raise RuntimeError(f'Camera {cam.index} has no working profiles')

    print('\n  可用配置（按实际帧率排序）：')
    # Sort by resolution descending, then fps descending.
    working_sorted = sorted(working, key=lambda p: (-p.actual_wh[0] * p.actual_wh[1], -p.real_fps))
    for i, p in enumerate(working_sorted, start=1):
        w, h = p.actual_wh
        print(f'    {i}: {w}x{h}  {p.fourcc}  {p.real_fps:.2f} fps')

    choice = _input_int('  选择配置编号', valid=set(range(1, len(working_sorted) + 1)))
    return working_sorted[choice - 1]


def interactive_configure(cameras: list[CameraInfo]) -> dict[int, dict]:
    """Return {arm_id: params} with camera_index, shape, target_fps, and format."""
    available = [c for c in cameras if c.available]
    if not available:
        print('No cameras available, nothing to assign.')
        return {}

    print('\n请为每个臂选择摄像头和录制参数。')
    print('建议：arm_2 分配给内窥镜（高分辨率/高帧率），arm_1/3 分配给双臂相机。')

    # Pre-compute suggestions: pick the camera with highest real fps for arm_2,
    # and remaining cameras for arm_1/3 if available.
    camera_indices = [c.index for c in available]
    suggestion: dict[int, int | None] = {1: None, 2: None, 3: None}
    if len(camera_indices) >= 1:
        suggestion[2] = camera_indices[0]
    if len(camera_indices) >= 2:
        suggestion[1] = camera_indices[1]
    if len(camera_indices) >= 3:
        suggestion[3] = camera_indices[2]

    camera_by_index = {c.index: c for c in available}
    result: dict[int, dict] = {}
    used_cameras: set[int] = set()

    for arm_id in (2, 1, 3):
        print(f'\n--- arm_{arm_id} ---')
        default_idx = suggestion[arm_id]
        # If the suggested camera is already used, offer any remaining.
        free = [c for c in available if c.index not in used_cameras]
        if not free:
            print('  没有可用摄像头，跳过。')
            continue

        print(f'  可用摄像头索引: {[c.index for c in free]}')
        if default_idx is not None and default_idx in used_cameras:
            default_idx = free[0].index

        while True:
            prompt = '  选择摄像头索引'
            valid = {c.index for c in free}
            cam_idx = _input_int(prompt, default=default_idx, valid=valid)
            if cam_idx in used_cameras:
                print(f'    摄像头 {cam_idx} 已被使用，请选择其他。')
                continue
            break

        used_cameras.add(cam_idx)
        cam = camera_by_index[cam_idx]

        profile = _pick_profile_for_camera(cam)
        w, h = profile.actual_wh
        max_fps = int(profile.real_fps)

        # Suggest a target output fps based on defaults, capped at measured max.
        suggested_target_fps = DEFAULT_ARM_SUGGESTIONS[arm_id]['target_fps']
        if suggested_target_fps > max_fps:
            suggested_target_fps = max(1, max_fps)

        print(f'\n  该配置实测最大帧率: {profile.real_fps:.2f} fps')
        while True:
            target_fps = _input_int('  目标录制帧率', default=suggested_target_fps)
            if 1 <= target_fps <= max_fps + 2:  # allow tiny rounding tolerance
                break
            print(f'    请输入 1-{max_fps} 之间的值。')

        image_format = _input_choice(
            '  图像保存格式',
            IMAGE_FORMAT_OPTIONS,
            default=DEFAULT_ARM_SUGGESTIONS[arm_id]['image_format'],
        )

        result[arm_id] = {
            'camera_index': cam_idx,
            'width': w,
            'height': h,
            'target_fps': target_fps,
            'fourcc': profile.fourcc,
            'image_format': image_format,
        }

    return result


def update_config(config_path: Path, arm_params: dict[int, dict]):
    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    sources = data.get('sources', [])
    camera_bindings = data.setdefault('camera_bindings', {})
    backup_path = config_path.with_suffix(config_path.suffix + '.bak')
    if not backup_path.exists():
        shutil.copy2(config_path, backup_path)
        print(f'Backup saved: {backup_path}')

    camera_sources: dict[int, dict] = {}
    for src in sources:
        arm_id = src.get('arm_id')
        if arm_id in (1, 2, 3):
            camera_sources[arm_id] = src

    for arm_id in (1, 2, 3):
        if arm_id not in camera_sources:
            new_src = {
                'type': 'process_camera',
                'name': f'camera_arm_{arm_id}',
                'topic_name': f'camera/arm_{arm_id}/image_raw',
                'arm_id': arm_id,
                'ready_timeout': 20.0,
            }
            sources.append(new_src)
            camera_sources[arm_id] = new_src

    for arm_id, src in camera_sources.items():
        if arm_id in arm_params:
            params = arm_params[arm_id]
            src['enabled'] = True
            src['type'] = 'process_camera'
            src['name'] = f'camera_arm_{arm_id}'
            src['topic_name'] = f'camera/arm_{arm_id}/image_raw'
            src['arm_id'] = arm_id
            src['backend'] = 'dshow'
            src['ready_timeout'] = 20.0
            src.pop('camera_index', None)
            camera_bindings[f'arm_{arm_id}'] = params['camera_index']
            src['width'] = params['width']
            src['height'] = params['height']
            # `fps` selects the driver streaming mode; `target_fps` is the
            # exact output rate produced by the software decimator. Preserve an
            # existing tuned driver mode and only provide one for new sources.
            if src.get('fps') is None:
                src['fps'] = 60 if params['target_fps'] >= 30 else params['target_fps']
            src['target_fps'] = params['target_fps']
            src['fourcc'] = params['fourcc']
            src['image_format'] = params['image_format']
            if params['image_format'] == 'png':
                src.setdefault('png_compression', 1)
                if params['width'] * params['height'] >= 1280 * 720:
                    src.setdefault('writer_threads', 2)
        else:
            camera_bindings.pop(f'arm_{arm_id}', None)
            src['enabled'] = False

    data['camera_bindings'] = camera_bindings
    data['sources'] = sources
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, sort_keys=False, allow_unicode=True)
    print(f'\nUpdated config: {config_path}')


def main():
    parser = argparse.ArgumentParser(description='Interactive camera-to-arm assignment')
    parser.add_argument('--config', type=str,
                        default=str(Path(__file__).parents[3] / 'scripts' / 'config' / 'default.yaml'),
                        help='Path to YAML config to update')
    parser.add_argument('--indices', type=int, nargs='+', default=list(range(10)),
                        help='Camera indices to probe (default: 0-9)')
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--out-dir', type=str, default='./var/camera_test_frames',
                        help='Directory for preview frames')
    parser.add_argument('--benchmark-duration', type=float, default=2.0,
                        help='Seconds to benchmark each (resolution, fourcc) profile')
    args = parser.parse_args()

    config_path = Path(args.config)
    out_dir = Path(args.out_dir)

    cameras = probe_cameras(args.indices, args.backend, out_dir,
                            benchmark_duration_s=args.benchmark_duration)
    print_summary(cameras)

    arm_params = interactive_configure(cameras)
    if not arm_params:
        print('No assignments made, config not changed.')
        return 0

    print('\nAssignments:')
    for arm_id, params in sorted(arm_params.items()):
        print(
            f'  arm_{arm_id} <- camera {params["camera_index"]}  '
            f'{params["width"]}x{params["height"]} @ {params["target_fps"]} fps  '
            f'{params["fourcc"]}  {params["image_format"]}'
        )

    update_config(config_path, arm_params)
    print('\nYou can now run:')
    print(f'  python run_acquisition.py --config {config_path} --duration 60')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
