"""GUI camera-to-arm assignment setup.

Opens a tkinter window showing a preview frame for each detected camera and
lets you pick which arm each camera belongs to. Updates the acquisition YAML
config when you click Save.

Usage:
    python -m vlgp.capture.camera_assignment_gui
    python -m vlgp.capture.camera_assignment_gui --config config/default.yaml
"""
from __future__ import annotations

import argparse
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

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

BACKENDS = {'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF, 'any': cv2.CAP_ANY}

DEFAULT_ARM_RESOLUTIONS = {
    1: (640, 480),
    2: (1280, 720),
    3: (640, 480),
}

THUMB_MAX_WIDTH = 400


def save_thumbnail(frame, out_path: Path, max_width: int = THUMB_MAX_WIDTH) -> Path:
    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        thumb = cv2.resize(frame, (max_width, int(h * scale)))
    else:
        thumb = frame
    cv2.imwrite(str(out_path), thumb)
    return out_path


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
        self.thumb_path: Path | None = None
        self.frame = None

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

        for w, h in COMMON_RESOLUTIONS:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            time.sleep(0.2)
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            ret, f = cap.read()
            self.resolutions.append({
                'requested': (w, h),
                'actual': (actual_w, actual_h),
                'fps': actual_fps,
                'frame_read': ret and f is not None and f.size > 0,
            })
        cap.release()
        return True

    def capture_preview(self, out_dir: Path) -> bool:
        if not self.available:
            return False
        backend = BACKENDS.get(self.backend_name, cv2.CAP_DSHOW)
        working = [r for r in self.resolutions if r['frame_read']]
        if working:
            best = max(working, key=lambda r: r['actual'][0] * r['actual'][1])
            w, h = best['requested']
        else:
            w, h = self.default_width, self.default_height

        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            return False
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
            return False

        self.frame = frame
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        preview_name = f'cam{self.index}_{self.backend_name}_{actual_w}x{actual_h}_{timestamp}.png'
        thumb_name = f'cam{self.index}_{self.backend_name}_{actual_w}x{actual_h}_{timestamp}_thumb.png'
        self.preview_path = out_dir / preview_name
        self.thumb_path = out_dir / thumb_name
        cv2.imwrite(str(self.preview_path), frame)
        save_thumbnail(frame, self.thumb_path)
        return True


def probe_cameras(indices: list[int], backend_name: str, out_dir: Path) -> list[CameraInfo]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cameras: list[CameraInfo] = []
    for idx in indices:
        cam = CameraInfo(idx, backend_name)
        if cam.probe():
            cam.capture_preview(out_dir)
        cameras.append(cam)
    return cameras


def update_config(config_path: Path, assignment: dict[int, int]):
    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    sources = data.get('sources', [])
    camera_bindings = data.setdefault('camera_bindings', {})
    backup_path = config_path.with_suffix(config_path.suffix + '.bak')
    if not backup_path.exists():
        import shutil
        shutil.copy2(config_path, backup_path)

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
                'fps': 60,
                'target_fps': 30,
                'backend': 'dshow',
                'image_format': 'png',
                'ready_timeout': 20.0,
            }
            sources.append(new_src)
            camera_sources[arm_id] = new_src

    defaults = {
        1: {'width': 640, 'height': 480, 'fourcc': 'MJPG', 'image_format': 'png'},
        2: {'width': 1280, 'height': 720, 'fourcc': 'MJPG', 'image_format': 'png',
            'png_compression': 1, 'writer_threads': 2},
        3: {'width': 640, 'height': 480, 'fourcc': 'MJPG', 'image_format': 'png'},
    }

    for arm_id, src in camera_sources.items():
        if arm_id in assignment:
            cam_index = assignment[arm_id]
            src['enabled'] = True
            src.pop('camera_index', None)
            camera_bindings[f'arm_{arm_id}'] = cam_index
            src['type'] = 'process_camera'
            src['name'] = f'camera_arm_{arm_id}'
            src['topic_name'] = f'camera/arm_{arm_id}/image_raw'
            src['arm_id'] = arm_id
            # Preserve an existing tuned driver mode; target_fps remains the
            # exact output rate. New sources default to a 60 fps stream
            # decimated to 30 fps.
            if src.get('fps') is None:
                src['fps'] = 60
            src['target_fps'] = src.get('target_fps', 30)
            src['backend'] = 'dshow'
            src['ready_timeout'] = 20.0
            src.update(defaults[arm_id])
        else:
            camera_bindings.pop(f'arm_{arm_id}', None)
            src['enabled'] = False

    data['camera_bindings'] = camera_bindings
    data['sources'] = sources
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, sort_keys=False, allow_unicode=True)


class AssignmentApp:
    def __init__(self, root: tk.Tk, cameras: list[CameraInfo], config_path: Path):
        self.root = root
        self.cameras = cameras
        self.config_path = config_path
        self.choices: dict[int, tk.StringVar] = {}

        root.title('VLGP 摄像头分配设置')
        root.geometry('1400x800')
        root.minsize(1000, 600)

        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            main_frame,
            text='请为每个摄像头选择对应的臂（arm_2 建议分配给内窥镜）',
            font=('Microsoft YaHei', 12),
        ).pack(pady=(0, 10))

        grid = ttk.Frame(main_frame)
        grid.pack(fill=tk.BOTH, expand=True)

        available = [c for c in cameras if c.available]
        cols = 3
        for idx, cam in enumerate(available):
            row = idx // cols
            col = idx % cols
            frame = ttk.Frame(grid, padding=10, relief=tk.RIDGE)
            frame.grid(row=row, column=col, padx=10, pady=5, sticky='nsew')
            grid.columnconfigure(col, weight=1)

            ttk.Label(frame, text=f'Camera {cam.index}', font=('Microsoft YaHei', 10, 'bold')).pack()

            if cam.thumb_path and cam.thumb_path.exists():
                img = tk.PhotoImage(file=str(cam.thumb_path))
                lbl = ttk.Label(frame, image=img)
                lbl.image = img  # keep reference
                lbl.pack(pady=5)
            else:
                ttk.Label(frame, text='无法获取预览').pack(pady=5)

            info_text = f'默认: {cam.default_width}x{cam.default_height} @ {cam.default_fps:.1f} fps\n'
            working = [r for r in cam.resolutions if r['frame_read'] and r['actual'] == r['requested']]
            if working:
                info_text += '支持分辨率: ' + ', '.join(f'{w}x{h}' for (_, _), (w, h) in [(r["requested"], r["actual"]) for r in working])
            ttk.Label(frame, text=info_text, justify=tk.LEFT).pack(pady=5)

            var = tk.StringVar(value='未分配')
            self.choices[cam.index] = var
            ttk.Label(frame, text='分配给:').pack()
            combo = ttk.Combobox(frame, textvariable=var, values=['未分配', 'arm_1', 'arm_2', 'arm_3'], state='readonly', width=12)
            combo.pack(pady=5)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text='保存配置', command=self.on_save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='取消', command=root.destroy).pack(side=tk.LEFT, padx=5)

        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=5)
        self.status = ttk.Label(status_frame, text='就绪')
        self.status.pack(side=tk.LEFT)

    def on_save(self):
        assignment: dict[int, int] = {}
        used_arms: set[int] = set()
        conflicts = []

        for cam_index, var in self.choices.items():
            val = var.get()
            if val == '未分配':
                continue
            arm_id = int(val.split('_')[1])
            if arm_id in used_arms:
                conflicts.append(arm_id)
            else:
                assignment[arm_id] = cam_index
                used_arms.add(arm_id)

        if conflicts:
            messagebox.showerror('冲突', f'以下臂被重复分配: {conflicts}')
            return

        if 2 not in assignment:
            if not messagebox.askyesno('确认', 'arm_2（内窥镜）未分配，是否继续保存？'):
                return

        update_config(self.config_path, assignment)
        self.status.config(text=f'已保存: {self.config_path}')
        messagebox.showinfo('完成', f'配置已更新\n{self.config_path}')
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description='GUI camera-to-arm assignment')
    parser.add_argument('--config', type=str,
                        default=str(Path(__file__).parents[3] / 'scripts' / 'config' / 'default.yaml'),
                        help='Path to YAML config file')
    parser.add_argument('--indices', type=int, nargs='+', default=list(range(10)),
                        help='Camera indices to probe (default: 0-9)')
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--out-dir', type=str, default='./var/camera_test_frames',
                        help='Directory for preview frames')
    args = parser.parse_args()

    config_path = Path(args.config)
    out_dir = Path(args.out_dir)

    print('正在探测摄像头，请稍候...')
    cameras = probe_cameras(args.indices, args.backend, out_dir)
    available = [c for c in cameras if c.available]
    if not available:
        messagebox.showerror('错误', '未检测到任何摄像头')
        return 1

    root = tk.Tk()
    app = AssignmentApp(root, cameras, config_path)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
