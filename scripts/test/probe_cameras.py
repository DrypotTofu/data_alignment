"""Probe available DirectShow camera indices."""
from __future__ import annotations

import sys
import time

import cv2


def probe_index(index: int, backend: int = cv2.CAP_DSHOW) -> dict | None:
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None
    time.sleep(0.5)
    ret, frame = cap.read()
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return {
        'index': index,
        'opened': True,
        'frame_read': ret,
        'width': width,
        'height': height,
        'fps': fps,
        'frame_shape': frame.shape if ret and frame is not None else None,
    }


def main():
    print('Probing DirectShow camera indices 0..5...')
    for i in range(6):
        info = probe_index(i)
        if info is None:
            print(f'  index {i}: NOT AVAILABLE')
        else:
            print(f'  index {i}: OK - {info["width"]}x{info["height"]} @ {info["fps"]:.2f} fps, frame_read={info["frame_read"]}')


if __name__ == '__main__':
    main()
