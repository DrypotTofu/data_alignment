"""Detect and list all available USB cameras.

Usage:
    python camera_detect.py
"""
import argparse

import cv2


def list_cameras(max_index: int = 10, backends: list[int] | None = None) -> list[dict]:
    """Probe camera indices 0..max_index and return openable devices.

    Tries multiple backends because some cameras only work with MSMF while
    others only work with DSHOW.
    """
    if backends is None:
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]

    cameras = []
    for idx in range(max_index):
        for backend in backends:
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                info = {
                    'index': idx,
                    'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    'fps': cap.get(cv2.CAP_PROP_FPS),
                    'backend': cap.getBackendName(),
                    'backend_code': backend,
                    'fourcc': int(cap.get(cv2.CAP_PROP_FOURCC)),
                }
                cameras.append(info)
                cap.release()
                break
            cap.release()
    return cameras


def print_cameras(cameras: list[dict]) -> None:
    if not cameras:
        print('No USB cameras detected.')
        return

    print(f'Detected {len(cameras)} camera(s):')
    print('-' * 60)
    for cam in cameras:
        fourcc_str = ' '.join(f'{((cam["fourcc"] >> 8 * i) & 0xFF):02x}' for i in range(4))
        print(f"  Index  : {cam['index']}")
        print(f"  Resolution: {cam['width']}x{cam['height']}")
        print(f"  Reported FPS: {cam['fps']:.2f}")
        print(f"  Backend: {cam['backend']} (code={cam['backend_code']})")
        print(f"  FOURCC : {fourcc_str}")
        print('-' * 60)


def main():
    parser = argparse.ArgumentParser(description='List available USB cameras.')
    parser.add_argument('--max-index', type=int, default=10,
                        help='Maximum camera index to probe (default: 10)')
    args = parser.parse_args()

    cameras = list_cameras(args.max_index)
    print_cameras(cameras)


if __name__ == '__main__':
    main()
