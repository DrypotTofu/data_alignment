"""Preview all connected USB/DirectShow cameras.

Scans camera indices, prints resolution/FPS capabilities, and saves a sample
frame for each working camera so you can visually identify which index
belongs to which physical camera (endoscope / left arm / right arm).

Usage:
    python tools/preview_connected_cameras.py
    python tools/preview_connected_cameras.py --indices 0 1 2 3 --out-dir ./camera_test_frames
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import cv2


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


def try_camera(index: int, backend_name: str = 'dshow'):
    """Open a camera and probe its default state plus common resolutions."""
    backend = BACKENDS.get(backend_name, cv2.CAP_DSHOW)
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None

    # Default state
    time.sleep(0.3)
    default_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    default_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    default_fps = cap.get(cv2.CAP_PROP_FPS)
    ret, default_frame = cap.read()
    cap.release()

    # Probe common resolutions
    resolutions = []
    for w, h in COMMON_RESOLUTIONS:
        cap = cv2.VideoCapture(index, backend)
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
        resolutions.append({
            'requested': (w, h),
            'actual': (actual_w, actual_h),
            'fps': actual_fps,
            'frame_read': ret and frame is not None and frame.size > 0,
        })

    return {
        'index': index,
        'default': {
            'width': default_w,
            'height': default_h,
            'fps': default_fps,
            'frame_read': ret and default_frame is not None and default_frame.size > 0,
        },
        'resolutions': resolutions,
    }


def save_preview_frame(index: int, width: int, height: int, backend_name: str, out_dir: Path) -> Path | None:
    """Save a single preview frame at the requested resolution."""
    backend = BACKENDS.get(backend_name, cv2.CAP_DSHOW)
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    time.sleep(0.5)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

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
    filename = f'cam{index}_{backend_name}_{actual_w}x{actual_h}_{timestamp}.png'
    out_path = out_dir / filename
    cv2.imwrite(str(out_path), frame)
    return out_path


def main():
    parser = argparse.ArgumentParser(description='Preview connected USB cameras')
    parser.add_argument('--indices', type=int, nargs='+', default=list(range(10)),
                        help='Camera indices to probe (default: 0-9)')
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--out-dir', type=str, default='./camera_test_frames',
                        help='Directory for saved preview frames')
    parser.add_argument('--save-csv', type=str, default=None,
                        help='Optional CSV path to write probe results')
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print('=' * 80)
    print('USB Camera Preview Report')
    print(f'Backend: {args.backend}')
    print(f'Saved frames: {out_dir.resolve()}')
    print('=' * 80)

    results = []
    for idx in args.indices:
        info = try_camera(idx, args.backend)
        if info is None:
            print(f'\nCamera index {idx}: NOT AVAILABLE')
            continue

        default = info['default']
        print(f'\nCamera index {idx}: AVAILABLE')
        print(f'  Default: {default["width"]}x{default["height"]} @ {default["fps"]:.2f} fps, frame_read={default["frame_read"]}')
        print('  Common resolutions:')
        working_resolutions = []
        for r in info['resolutions']:
            rw, rh = r['requested']
            aw, ah = r['actual']
            status = 'OK' if r['frame_read'] and (aw, ah) == (rw, rh) else 'PARTIAL' if r['frame_read'] else 'FAIL'
            print(f'    {rw}x{rh} -> {aw}x{ah} @ {r["fps"]:.2f} fps [{status}]')
            if r['frame_read']:
                working_resolutions.append(r)

        # Save a preview frame at the highest working resolution, or default.
        if working_resolutions:
            best = max(working_resolutions, key=lambda r: r['actual'][0] * r['actual'][1])
            save_w, save_h = best['requested']
        else:
            save_w, save_h = default['width'], default['height']

        preview_path = save_preview_frame(idx, save_w, save_h, args.backend, out_dir)
        if preview_path:
            print(f'  Preview saved: {preview_path.name}')
        else:
            print('  Preview save failed')

        results.append({
            'index': idx,
            'available': True,
            'default_width': default['width'],
            'default_height': default['height'],
            'default_fps': default['fps'],
            'preview_file': preview_path.name if preview_path else None,
        })

    if args.save_csv:
        csv_path = Path(args.save_csv)
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['index', 'available', 'default_width', 'default_height', 'default_fps', 'preview_file'])
            writer.writeheader()
            writer.writerows(results)
        print(f'\nCSV summary saved to {csv_path}')

    print('\n' + '=' * 80)
    print('Recommended configuration:')
    print('  - Endoscope (arm_2): use highest working resolution >= 1280x720')
    print('  - Arm cameras (arm_1 / arm_3): 640x480 is sufficient')
    print('=' * 80)


if __name__ == '__main__':
    raise SystemExit(main())
