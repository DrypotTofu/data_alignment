"""Shared camera probing primitives (CODE_REVIEW_20260830 2.2).

Before this module the same BACKENDS / COMMON_RESOLUTIONS / default-state
probe / per-resolution probe / preview-capture patterns were copy-pasted in
four capture tools (camera_assignment_gui, camera_assignment_setup,
preview_connected_cameras, preview_single_camera) with small drift between
them (one had a bug where the default frame's read result was overwritten by
a later resolution loop). Tools keep their tool-specific behaviour (the
real-FPS benchmark with FOURCC variants in camera_assignment_setup, the
interactive UI loops, their own config writers) and call these primitives.

All functions are camera-hardware helpers and may block on camera I/O.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2

# Canonical DirectShow/MediaFoundation backends shared by every capture tool.
BACKENDS = {'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF, 'any': cv2.CAP_ANY}

# Resolutions probed on every camera (largest first).
COMMON_RESOLUTIONS = [
    (1920, 1080),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 480),
    (320, 240),
]

DEFAULT_SETTLE_S = 0.3     # wait after opening/setting a mode
PROBE_SETTLE_S = 0.2       # wait after setting W/H per resolution
PREVIEW_SETTLE_S = 0.5     # wait before grabbing the preview frame
PREVIEW_MAX_READS = 15     # attempts to grab a usable preview frame
THUMB_MAX_WIDTH = 400


def open_camera(index: int, backend_name: str = 'dshow') -> cv2.VideoCapture | None:
    """Open a camera with a named backend (falling back to CAP_DSHOW)."""
    backend = BACKENDS.get(backend_name, cv2.CAP_DSHOW)
    cap = cv2.VideoCapture(int(index), backend)
    if not cap.isOpened():
        cap.release()
        return None
    return cap


def probe_default_state(cap: cv2.VideoCapture) -> tuple[int, int, float]:
    """Return (width, height, fps) reported for a freshly opened camera."""
    time.sleep(DEFAULT_SETTLE_S)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    return w, h, fps


def probe_resolution(index: int, backend_name: str, width: int, height: int) -> dict:
    """Open the camera fresh and probe one (width, height) mode.

    Uses a fresh capture per resolution: some DirectShow drivers only
    renegotiate the mode when the device handle is reopened, so mutating one
    handle across resolutions reports stale sizes.
    """
    cap = open_camera(index, backend_name)
    if cap is None:
        return {'requested': (width, height), 'actual': (0, 0),
                'fps': 0.0, 'frame_read': False}
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        time.sleep(PROBE_SETTLE_S)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        ret, frame = cap.read()
        return {
            'requested': (width, height),
            'actual': (actual_w, actual_h),
            'fps': actual_fps,
            'frame_read': ret and frame is not None and frame.size > 0,
        }
    finally:
        cap.release()


def probe_common_resolutions(index: int, backend_name: str = 'dshow') -> list[dict]:
    """Probe every COMMON_RESOLUTIONS mode of one camera index."""
    return [probe_resolution(index, backend_name, w, h)
            for w, h in COMMON_RESOLUTIONS]


def best_working(entries: list[dict]) -> dict | None:
    """Largest actual area among resolution-probe entries with frame_read."""
    working = [e for e in entries if e.get('frame_read')]
    if not working:
        return None
    return max(working, key=lambda e: e['actual'][0] * e['actual'][1])


def grab_frame(
    index: int,
    width: int,
    height: int,
    backend_name: str,
    fourcc: str | None = None,
    settle_s: float = PREVIEW_SETTLE_S,
):
    """Open a camera, set (width, height[, fourcc]) and return one frame.

    Returns the BGR frame, or None if the camera could not be opened or no
    usable frame was read.
    """
    cap = open_camera(index, backend_name)
    if cap is None:
        return None
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc[:4]))
        time.sleep(settle_s)
        for _ in range(PREVIEW_MAX_READS):
            ret, f = cap.read()
            if ret and f is not None and f.size > 0:
                return f
            time.sleep(0.05)
        return None
    finally:
        cap.release()


def capture_preview_frame(
    index: int,
    width: int,
    height: int,
    backend_name: str,
    out_dir: Path,
    fourcc: str | None = None,
) -> Path | None:
    """Save one PNG frame of a camera at (width, height[, fourcc]).

    Returns the saved path, or None if the camera could not be opened or no
    frame was read.
    """
    frame = grab_frame(index, width, height, backend_name, fourcc=fourcc)
    if frame is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    actual_h, actual_w = frame.shape[:2]
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename = f'cam{index}_{backend_name}_{actual_w}x{actual_h}_{timestamp}.png'
    out_path = out_dir / filename
    cv2.imwrite(str(out_path), frame)
    return out_path


def save_thumbnail(frame, out_path: Path, max_width: int = THUMB_MAX_WIDTH) -> Path:
    """Downscale (if wider than max_width) and save a frame as a thumbnail."""
    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        thumb = cv2.resize(frame, (max_width, int(h * scale)))
    else:
        thumb = frame
    cv2.imwrite(str(out_path), thumb)
    return out_path
