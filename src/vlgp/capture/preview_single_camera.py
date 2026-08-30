"""Live preview USB cameras for focus/position adjustment.

Shows a real-time OpenCV window with the current resolution, FPS and camera
index overlaid. Press 'q' or ESC to close the current preview; the script then
asks whether to preview another camera.

Usage:
    python -m vlgp.capture.preview_single_camera
    python -m vlgp.capture.preview_single_camera --index 0 --width 1280 --height 720
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2


BACKENDS = {'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF, 'any': cv2.CAP_ANY}


def probe_cameras(indices: list[int], backend_name: str = 'dshow') -> dict[int, tuple[int, int]]:
    """Return {index: (width, height)} for available cameras."""
    backend = BACKENDS.get(backend_name, cv2.CAP_DSHOW)
    found: dict[int, tuple[int, int]] = {}
    for idx in indices:
        cap = cv2.VideoCapture(idx, backend)
        if cap.isOpened():
            time.sleep(0.3)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found[idx] = (w, h)
        cap.release()
    return found


def select_camera_interactively(found: dict[int, tuple[int, int]]) -> int:
    print('\n可用摄像头:')
    for idx, (w, h) in sorted(found.items()):
        print(f'  [{idx}] {w}x{h}')

    while True:
        print('\n请输入要预览的摄像头索引: ', end='', flush=True)
        choice = input().strip()
        if choice.isdigit() and int(choice) in found:
            return int(choice)
        print('输入无效，请重新输入。')


def preview_camera(index: int, args) -> bool:
    """Open one camera preview. Return True if preview ran, False on fatal error."""
    backend = BACKENDS[args.backend]
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        print(f'错误: 无法打开摄像头 index {index}')
        return False

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    if args.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc[:4]))

    time.sleep(0.5)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    snapshot_dir = Path(args.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    window_name = f'Camera {index} preview - q:quit  s:snapshot'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    frame_count = 0
    start_time = time.time()
    snapshot_count = 0

    print(f'\n摄像头 {index} 已打开: {actual_w}x{actual_h} @ {actual_fps:.1f} fps')
    print("按 'q' 或 ESC 退出当前预览，按 's' 保存快照")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            print('读取帧失败，退出')
            break

        # Detect if the user closed the window with the X button.
        try:
            visible = cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE)
            if visible < 1:
                print('预览窗口已关闭')
                break
        except cv2.error:
            break

        frame_count += 1
        elapsed = time.time() - start_time
        fps = frame_count / elapsed if elapsed > 0 else 0.0

        text_lines = [
            f'Camera {index}  {actual_w}x{actual_h}',
            f'FPS: {fps:.1f}  FourCC: {args.fourcc}',
            'q/ESC/X: quit   s: snapshot',
        ]
        y0 = 30
        for i, line in enumerate(text_lines):
            y = y0 + i * 30
            cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 0), 2, cv2.LINE_AA)

        cv2.imshow(window_name, frame)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), 27):  # q or ESC
            break
        if key == ord('s'):
            snapshot_count += 1
            filename = snapshot_dir / f'cam{index}_snapshot_{snapshot_count:03d}_{int(time.time())}.png'
            cv2.imwrite(str(filename), frame)
            print(f'快照已保存: {filename}')

    cap.release()
    cv2.destroyAllWindows()
    print(f'摄像头 {index} 预览已关闭')
    return True


def ask_yes_no(prompt: str) -> bool:
    while True:
        print(prompt, end='', flush=True)
        choice = input().strip().lower()
        if choice in ('y', 'yes', '是', '1'):
            return True
        if choice in ('n', 'no', '否', '0', ''):
            return False
        print('输入无效，请输入 y/n 或 是/否。')


def main() -> int:
    parser = argparse.ArgumentParser(description='Live preview USB cameras')
    parser.add_argument('--index', type=int, default=None,
                        help='Camera index to preview (if omitted, interactive selection)')
    parser.add_argument('--width', type=int, default=1280,
                        help='Requested width')
    parser.add_argument('--height', type=int, default=720,
                        help='Requested height')
    parser.add_argument('--fps', type=int, default=30,
                        help='Requested FPS')
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--fourcc', type=str, default='MJPG',
                        help='FOURCC codec, e.g. MJPG or YUY2')
    parser.add_argument('--snapshot-dir', type=str, default='./var/camera_test_frames',
                        help='Directory for saved snapshots')
    args = parser.parse_args()

    print('正在探测摄像头...')
    found = probe_cameras(list(range(10)), args.backend)
    if not found:
        print('错误: 未检测到任何摄像头')
        return 1

    if args.index is not None:
        if args.index not in found:
            print(f'错误: 摄像头 index {args.index} 不可用')
            return 1
        preview_camera(args.index, args)
        return 0

    while True:
        index = select_camera_interactively(found)
        preview_camera(index, args)
        if not ask_yes_no('\n是否预览其他摄像头? (y/n): '):
            break

    print('全部预览结束')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
