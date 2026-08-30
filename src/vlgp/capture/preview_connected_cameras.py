"""Preview all connected USB/DirectShow cameras.

Scans camera indices, prints resolution/FPS capabilities, and saves a sample
frame for each working camera so you can visually identify which index
belongs to which physical camera (endoscope / left arm / right arm).

Usage:
    python -m vlgp.capture.preview_connected_cameras
    python -m vlgp.capture.preview_connected_cameras --indices 0 1 2 3 --out-dir ./camera_test_frames
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from vlgp.capture.camera_probe import (
    BACKENDS,
    best_working,
    capture_preview_frame,
    open_camera,
    probe_common_resolutions,
    probe_default_state,
)


def try_camera(index: int, backend_name: str = 'dshow'):
    """Open a camera and probe its default state plus common resolutions."""
    cap = open_camera(index, backend_name)
    if cap is None:
        return None
    try:
        default_w, default_h, default_fps = probe_default_state(cap)
        default_ret, default_frame = cap.read()
    finally:
        cap.release()

    resolutions = probe_common_resolutions(index, backend_name)
    return {
        'index': index,
        'default': {
            'width': default_w,
            'height': default_h,
            'fps': default_fps,
            'frame_read': default_ret and default_frame is not None and default_frame.size > 0,
        },
        'resolutions': resolutions,
    }


def main():
    parser = argparse.ArgumentParser(description='Preview connected USB cameras')
    parser.add_argument('--indices', type=int, nargs='+', default=list(range(10)),
                        help='Camera indices to probe (default: 0-9)')
    parser.add_argument('--backend', type=str, default='dshow', choices=list(BACKENDS.keys()))
    parser.add_argument('--out-dir', type=str, default='./var/camera_test_frames',
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
        for r in info['resolutions']:
            rw, rh = r['requested']
            aw, ah = r['actual']
            status = 'OK' if r['frame_read'] and (aw, ah) == (rw, rh) else 'PARTIAL' if r['frame_read'] else 'FAIL'
            print(f'    {rw}x{rh} -> {aw}x{ah} @ {r["fps"]:.2f} fps [{status}]')

        # Save a preview frame at the highest working resolution, or default.
        best = best_working(info['resolutions'])
        if best:
            save_w, save_h = best['requested']
        else:
            save_w, save_h = default['width'], default['height']

        preview_path = capture_preview_frame(idx, save_w, save_h, args.backend, out_dir)
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
