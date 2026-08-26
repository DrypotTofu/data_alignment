"""JPEG preview frame service (T15 v0).

Serves alignment-row frames as downscaled JPEGs for the web frontend
(canvas frame-index rendering + prefetch sliding window). Decoded frames
are cached so a playback window is served without re-reading PNGs.
"""
from __future__ import annotations

from functools import lru_cache

import cv2

from vlgp.web.session_index import SessionIndex

JPEG_QUALITY = 82
DEFAULT_MAX_WIDTH = 960


class FrameService:
    """Frame server bound to one SessionIndex."""

    def __init__(self, index: SessionIndex):
        self.index = index

    def jpeg(self, session: str, cam: str, row: int,
             max_width: int = DEFAULT_MAX_WIDTH) -> bytes:
        """JPEG bytes for one alignment row of a camera (cached)."""
        max_width = int(max_width)
        return _jpeg_cached(self.index, session, cam, int(row), max_width)

    def batch(self, session: str, cam: str, rows: list[int],
              max_width: int = DEFAULT_MAX_WIDTH) -> dict[int, str]:
        """base64 JPEG map row -> data URI for a prefetch window."""
        out: dict[int, str] = {}
        for r in rows:
            try:
                data = self.jpeg(session, cam, r, max_width)
            except (IndexError, FileNotFoundError, ValueError):
                continue
            out[int(r)] = 'data:image/jpeg;base64,' + _b64(data)
        return out


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode('ascii')


@lru_cache(maxsize=768)
def _jpeg_cached(index: SessionIndex, session: str, cam: str, row: int,
                 max_width: int) -> bytes:
    fname = index.frame_filename(session, cam, row)
    img = cv2.imread(str(index.session_dir(session) / cam / fname))
    if img is None:
        raise FileNotFoundError(f'{session}/{cam}/{fname} unreadable')
    h, w = img.shape[:2]
    if w > max_width:
        scale = max_width / w
        img = cv2.resize(img, (max_width, max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError('JPEG encode failed')
    return buf.tobytes()
