"""JPEG preview frame service (T15 v0).

Serves alignment-row frames as downscaled JPEGs for the web frontend
(canvas frame-index rendering + whole-session preload). Encoded JPEGs are
written through to a per-session DISK cache (var/web_preview), so a session
is encoded exactly once: every later request (playback, seek, preload) is a
fast disk read of a small JPEG instead of a full PNG decode.

Whole-session preload: ``PreloadRegistry.start`` encodes every row of all
cameras in the background (one 10-minute segment at a time for long
sessions), with progress consumed by the web player's progress bar.

Performance (measured 2026-08-28 on a 720p/480p session):
  serial cold encode ~17 ms/row (arm2) / ~5 ms/row (wrists); with the
  4-worker thread pool the batch endpoint sustains ~190 / ~600 rows/s,
  well above the 30 rows/s playback rate, so the client's lookahead
  preloader can keep the whole preview window warm.
"""
from __future__ import annotations

import os
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from math import ceil
from pathlib import Path

import cv2

from vlgp.web.session_index import CAMERA_ORDER, SessionIndex

JPEG_QUALITY = 82
DEFAULT_MAX_WIDTH = 960
_BATCH_WORKERS = 4
PRELOAD_WORKERS = 4
PRELOAD_SEGMENT = 18000          # one preload segment = 10 minutes @ 30 Hz
PREVIEW_MAX_W = {'camera_arm_2': 640, 'camera_arm_1': 320, 'camera_arm_3': 320}
_PREVIEW_ROOT = Path(__file__).resolve().parents[3] / 'var' / 'web_preview'


class FrameService:
    """Frame server bound to one SessionIndex."""

    def __init__(self, index: SessionIndex):
        self.index = index

    def jpeg(self, session: str, cam: str, row: int,
             max_width: int = DEFAULT_MAX_WIDTH) -> bytes:
        """JPEG bytes for one alignment row of a camera (RAM + disk cached)."""
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

    def batch_bin(self, session: str, cam: str, rows: list[int],
                  max_width: int = DEFAULT_MAX_WIDTH) -> bytes:
        """Binary frame batch: int32 count, then per image int32 row, int32
        length, raw JPEG bytes (little-endian).

        The web player uses this instead of the base64-JSON batch: the client
        parses the ArrayBuffer and builds Blob/object URLs directly, keeping
        the main thread free of JSON.parse/base64 work during playback.
        """
        if not rows:
            return struct.pack('<i', 0)

        def one(r: int):
            try:
                return int(r), self.jpeg(session, cam, int(r), max_width)
            except (IndexError, FileNotFoundError, ValueError):
                return None

        items = [it for it in _EXECUTOR.map(one, rows) if it is not None]
        out = bytearray(struct.pack('<i', len(items)))
        for row, data in items:
            out += struct.pack('<ii', row, len(data))
            out += data
        return bytes(out)


class PreloadRegistry:
    """Background whole-session preview preload with progress.

    One background thread encodes every row of every camera (skipping rows
    already on the disk cache, so restarts resume where they stopped) in
    segments of PRELOAD_SEGMENT rows (= 10 minutes @ 30 Hz); the web player
    polls ``status`` to drive its progress bar.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def start(self, frames: FrameService, session: str, total: int) -> dict:
        with self._lock:
            j = self._jobs.get(session)
            early = (j is not None and j['running']) or (
                j is not None and not j['running']
                and j['total'] > 0 and j['current'] >= j['total'])
            if not early:
                segments = max(1, ceil(total / PRELOAD_SEGMENT))
                j = {'running': True, 'current': 0,
                     'total': total * len(CAMERA_ORDER),
                     'segment': 0, 'segments': segments}
                self._jobs[session] = j
        if early:
            return self._snapshot(j)          # already running / fully preloaded
        threading.Thread(target=self._run, args=(frames, session, total, j),
                         daemon=True).start()
        return self._snapshot(j)

    def status(self, session: str) -> dict:
        with self._lock:
            j = self._jobs.get(session)
        if not j:
            return {'status': 'idle', 'current': 0, 'total': 0,
                    'segment': 0, 'segments': 1}
        return self._snapshot(j)

    # ------------------------------------------------------------------ impl
    # Snapshot WITHOUT taking the lock: it only reads int/bool fields of a
    # job dict (atomic under the GIL); callers take the lock to pick the
    # dict, never to read inside it (avoids non-reentrant Lock deadlocks).
    def _snapshot(self, j: dict) -> dict:
        return {'status': 'running' if j['running'] else 'done',
                'current': j['current'], 'total': j['total'],
                'segment': j['segment'], 'segments': j['segments']}

    def _run(self, frames: FrameService, session: str, total: int, j: dict) -> None:
        pool = ThreadPoolExecutor(max_workers=PRELOAD_WORKERS)
        try:
            for seg in range(j['segments']):
                with self._lock:
                    j['segment'] = seg
                s0 = seg * PRELOAD_SEGMENT
                s1 = min(total, s0 + PRELOAD_SEGMENT)
                pairs = [(cam, r) for cam in CAMERA_ORDER for r in range(s0, s1)]
                missing = [p for p in pairs
                           if not _preview_path(session, p[0], p[1],
                                                PREVIEW_MAX_W[p[0]]).exists()]
                # rows already on disk count as done immediately
                with self._lock:
                    j['current'] += len(pairs) - len(missing)
                # encode missing rows in chunks, yielding between chunks so
                # the server stays responsive to the player's own requests
                step = 256
                for i in range(0, len(missing), step):
                    chunk = missing[i:i + step]

                    def one(item):
                        cam, r = item
                        try:
                            # bypass the LRU so the pool's workers parallelize
                            _encode_preview(frames.index, session, cam, r,
                                            PREVIEW_MAX_W[cam])
                            return 1
                        except Exception:  # noqa: BLE001 — bad row, skip
                            return 0

                    done = 0
                    if chunk:
                        done = sum(pool.map(one, chunk))
                    with self._lock:
                        j['current'] += done
                    time.sleep(0.02)
        finally:
            with self._lock:
                j['current'] = j['total']
                j['running'] = False
            pool.shutdown(wait=False)


def _preview_path(session: str, cam: str, row: int, max_width: int) -> Path:
    return (_PREVIEW_ROOT / _safe_part(session) / cam / f'w{int(max_width)}'
            / f'{int(row)}.jpg')


def _safe_part(name: str) -> str:
    base = os.path.basename(name)
    return base if base and base not in ('.', '..') else 'invalid'


_EXECUTOR = ThreadPoolExecutor(max_workers=_BATCH_WORKERS)


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode('ascii')


@lru_cache(maxsize=2048)
def _jpeg_cached(index: SessionIndex, session: str, cam: str, row: int,
                 max_width: int) -> bytes:
    # disk cache first: a preloaded session is served by small JPEG reads
    dpath = _preview_path(session, cam, row, max_width)
    try:
        return dpath.read_bytes()
    except OSError:
        pass
    return _encode_preview(index, session, cam, row, max_width)


def _encode_preview(index: SessionIndex, session: str, cam: str, row: int,
                    max_width: int) -> bytes:
    """Decode + downscale + JPEG-encode one row and write it to the disk
    cache. Bypasses the LRU so background preload workers actually run in
    parallel (the LRU holds its lock for the whole wrapped call)."""
    dpath = _preview_path(session, cam, row, max_width)
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
    data = buf.tobytes()
    # write-through so the session needs to be encoded only once ever
    try:
        dpath.parent.mkdir(parents=True, exist_ok=True)
        dpath.write_bytes(data)
    except OSError:
        pass
    return data
