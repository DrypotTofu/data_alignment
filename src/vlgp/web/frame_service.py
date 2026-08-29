"""JPEG preview frame service (T15 v0).

Serves alignment-row frames as downscaled JPEGs for the web frontend
(canvas frame-index rendering + prefetch sliding window). Decoded frames
are cached so a playback window is served without re-reading PNGs.

Performance (measured 2026-08-28 on a 720p/480p session):
  serial cold encode ~17 ms/row (arm2) / ~5 ms/row (wrists); with the
  4-worker thread pool the batch endpoint sustains ~190 / ~600 rows/s,
  well above the 30 rows/s playback rate, so the client's lookahead
  preloader can keep the whole preview window warm.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import cv2

from vlgp.web.session_index import SessionIndex

JPEG_QUALITY = 82
DEFAULT_MAX_WIDTH = 960
_BATCH_WORKERS = 4


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
        """base64 JPEG map row -> data URI for a prefetch window.

        Rows are encoded in parallel (cold PNG decode + JPEG encode is the
        dominant cost; OpenCV releases the GIL so a small thread pool gives
        ~3x throughput and keeps up with 30 fps lookahead preloading).
        """
        out: dict[int, str] = {}
        if not rows:
            return out

        def one(r: int):
            try:
                data = self.jpeg(session, cam, int(r), max_width)
                return int(r), 'data:image/jpeg;base64,' + _b64(data)
            except (IndexError, FileNotFoundError, ValueError):
                return None

        for item in _EXECUTOR.map(one, rows):
            if item is not None:
                out[item[0]] = item[1]
        return out


_EXECUTOR = ThreadPoolExecutor(max_workers=_BATCH_WORKERS)


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode('ascii')


@lru_cache(maxsize=2048)
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
