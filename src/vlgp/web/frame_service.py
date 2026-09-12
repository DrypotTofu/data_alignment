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
import shutil
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from math import ceil
from pathlib import Path

import cv2

from vlgp.web.session_index import CAMERA_ORDER, FILENAME_COLS, SessionIndex

JPEG_QUALITY = 82
DEFAULT_MAX_WIDTH = 960
_BATCH_WORKERS = 4
PRELOAD_WORKERS = 12         # 16-core host: preload uses most cores; batch stays free
PRELOAD_SEGMENT = 18000          # one preload segment = 10 minutes @ 30 Hz
PREVIEW_MAX_W = {'camera_arm_2': 640, 'camera_arm_1': 320, 'camera_arm_3': 320}
# The preview cache follows the ACTIVE data root (<root>/_web_preview) — a
# nearly-full repo disk must not be filled by browsing a big external disk.
# When the data root is not writable (archive/media), fall back to the repo
# var/web_preview so playback still gets a disk cache.
_FALLBACK_PREVIEW_ROOT = Path(__file__).resolve().parents[3] / 'var' / 'web_preview'


class FrameService:
    """Frame server bound to one SessionIndex (which owns the active root)."""

    def __init__(self, index: SessionIndex):
        self.index = index
        self._cache_dirs: dict[str, Path] = {}

    def cache_dir(self) -> Path:
        """Disk cache directory for the current data root (memoized per root).

        ``<root>/_web_preview`` when the root accepts writes, otherwise the
        repo ``var/web_preview``; a root that cannot be written at all (e.g. a
        read-only archive mount) degrades to the in-RAM LRU only.
        """
        root = str(self.index.root)
        hit = self._cache_dirs.get(root)
        if hit is not None:
            return hit
        cand = self.index.preview_root
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / '.write_probe'
            probe.write_bytes(b'')
            probe.unlink()
        except OSError:
            cand = _FALLBACK_PREVIEW_ROOT
        self._cache_dirs[root] = cand
        return cand

    def jpeg(self, session: str, cam: str, row: int,
             max_width: int = DEFAULT_MAX_WIDTH) -> bytes:
        """JPEG bytes for one alignment row of a camera (RAM + disk cached)."""
        max_width = int(max_width)
        return _jpeg_cached(self.index, str(self.cache_dir()), session, cam,
                            int(row), max_width)

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
        # Legacy/single-camera sessions carry only some of the per-camera
        # filename columns; preloading the missing ones used to raise
        # KeyError inside the worker thread and silently preload nothing.
        cams = _available_cameras(frames.index, session)
        with self._lock:
            j = self._jobs.get(session)
            early = (j is not None and j['running']) or (
                j is not None and not j['running']
                and j['total'] > 0 and j['current'] >= j['total'])
            if not early:
                segments = max(1, ceil(total / PRELOAD_SEGMENT))
                j = {'running': True, 'current': 0,
                     'total': total * len(cams), 'cams': cams,
                     'segment': 0, 'segments': segments, 'error': None}
                self._jobs[session] = j
        if early:
            return self._snapshot(j)          # already running / fully preloaded
        threading.Thread(target=self._run, args=(frames, session, total, j, cams),
                         daemon=True).start()
        return self._snapshot(j)

    def status(self, session: str) -> dict:
        with self._lock:
            j = self._jobs.get(session)
        if not j:
            return {'status': 'idle', 'current': 0, 'total': 0,
                    'segment': 0, 'segments': 1}
        return self._snapshot(j)

    def reset(self) -> None:
        """Drop all job state (data-root switch: job keys are session names,
        which can collide across roots). Running jobs are not interrupted —
        the API refuses a switch while one runs (see ``running``)."""
        with self._lock:
            self._jobs.clear()

    def running(self) -> list[str]:
        """Sessions with a preload still running (root-switch guard)."""
        with self._lock:
            return [k for k, j in self._jobs.items() if j['running']]

    # ------------------------------------------------------------------ impl
    # Snapshot WITHOUT taking the lock: it only reads int/bool fields of a
    # job dict (atomic under the GIL); callers take the lock to pick the
    # dict, never to read inside it (avoids non-reentrant Lock deadlocks).
    def _snapshot(self, j: dict) -> dict:
        if j.get('error'):
            status = 'error'
        else:
            status = 'running' if j['running'] else 'done'
        return {'status': status, 'current': j['current'], 'total': j['total'],
                'segment': j['segment'], 'segments': j['segments'],
                'cams': j.get('cams', []), 'error': j.get('error')}

    def _run(self, frames: FrameService, session: str, total: int, j: dict,
             cams: list[str]) -> None:
        # NOTE (三.9): a fresh 12-worker pool per preload job — concurrent
        # preloads of several sessions would stack pools. Acceptable for the
        # single-user web platform; revisit if multi-session preload lands.
        pool = ThreadPoolExecutor(max_workers=PRELOAD_WORKERS)
        try:
            # Precompute per-cam filenames ONCE: df.iloc[row] is ~220 us/row,
            # so 40k rows would otherwise cost ~9 s of pandas lookups.
            df = frames.index.alignment(session)
            session_dir = frames.index.session_dir(session)
            cache_root = frames.cache_dir()
            fnames = {cam: df[FILENAME_COLS[cam]].astype(str).tolist()
                      for cam in cams}
            for seg in range(j['segments']):
                with self._lock:
                    j['segment'] = seg
                s0 = seg * PRELOAD_SEGMENT
                s1 = min(total, s0 + PRELOAD_SEGMENT)
                # one directory scan per cam replaces 40k Path.exists() stats
                existing = {cam: _preview_existing(cache_root, session, cam,
                                                   PREVIEW_MAX_W[cam])
                            for cam in cams}
                pairs = [(cam, r) for cam in cams for r in range(s0, s1)]
                missing = [p for p in pairs if p[1] not in existing[p[0]]]
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
                            # bypass the LRU so the pool's workers parallelize;
                            # filename precomputed once (no per-row pandas)
                            _encode_preview_fname(cache_root, session_dir,
                                                  fnames[cam][r], session, cam,
                                                  r, PREVIEW_MAX_W[cam])
                            return 1
                        except Exception:  # noqa: BLE001 — bad row, skip
                            return 0

                    done = 0
                    if chunk:
                        done = sum(pool.map(one, chunk))
                    with self._lock:
                        j['current'] += done
                    time.sleep(0.02)
        except Exception as e:  # noqa: BLE001 — never leave the job 'running'
            import traceback
            traceback.print_exc()
            with self._lock:
                j['error'] = f'{type(e).__name__}: {e}'
        finally:
            with self._lock:
                if not j.get('error'):
                    j['current'] = j['total']
                j['running'] = False
            pool.shutdown(wait=False)


def _available_cameras(index: SessionIndex, session: str) -> list[str]:
    """Cameras the alignment table actually carries a filename column for."""
    try:
        cols = set(index.alignment(session).columns)
    except Exception:  # noqa: BLE001 — surface as an empty preload
        return []
    return [c for c in CAMERA_ORDER if FILENAME_COLS[c] in cols]


def _preview_path(cache_root: Path, session: str, cam: str, row: int,
                  max_width: int) -> Path:
    return (cache_root / _safe_part(session) / cam / f'w{int(max_width)}'
            / f'{int(row)}.jpg')


def _safe_part(name: str) -> str:
    base = os.path.basename(name)
    return base if base and base not in ('.', '..') else 'invalid'


_EXECUTOR = ThreadPoolExecutor(max_workers=_BATCH_WORKERS)


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode('ascii')


def _preview_existing(cache_root: Path, session: str, cam: str,
                      max_width: int) -> set[int]:
    """Rows already on the disk cache for one camera — one directory scan
    replaces tens of thousands of per-row Path.exists() stats."""
    d = cache_root / _safe_part(session) / cam / f'w{int(max_width)}'
    try:
        return {int(p.stem) for p in d.iterdir() if p.suffix == '.jpg'}
    except OSError:
        return set()


@lru_cache(maxsize=2048)
def _jpeg_cached(index: SessionIndex, cache_root_str: str, session: str,
                 cam: str, row: int, max_width: int) -> bytes:
    # The on-disk cache dir is part of the key on purpose: the index object
    # outlives a data-root switch, so without it rows from the previous root
    # would be served for a same-named session.
    cache_root = Path(cache_root_str)
    dpath = _preview_path(cache_root, session, cam, row, max_width)
    data = _read_cached_jpeg(dpath)
    if data is not None:
        return data
    return _encode_preview_fname(cache_root, index.session_dir(session),
                                 index.frame_filename(session, cam, row),
                                 session, cam, row, max_width)


def _read_cached_jpeg(path: Path) -> bytes | None:
    """Cached bytes if they are a complete JPEG, else None.

    The cache lives on a data disk (external SSD / archive drive), so a crash
    or an unplug mid-write can leave a truncated file; serving it forever would
    show a broken frame with no way to recover except deleting the folder.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 4 or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        return None
    return data


def reset_frame_cache() -> None:
    """Drop the RAM JPEG cache (data-root switch: same session names may now
    resolve to different files, and the cached bytes belong to the old root)."""
    _jpeg_cached.cache_clear()


def cache_roots(index: SessionIndex) -> list[Path]:
    """Every directory a preview cache may live in (active + legacy fallback)."""
    out = [index.preview_root]
    if _FALLBACK_PREVIEW_ROOT != index.preview_root:
        out.append(_FALLBACK_PREVIEW_ROOT)
    return out


def _dir_stats(path: Path) -> tuple[int, int]:
    """(files, bytes) under ``path`` — see vlgp.web.fs_browse.dir_stats
    (os.scandir + DirEntry.stat: no syscall per file)."""
    from vlgp.web.fs_browse import dir_stats
    return dir_stats(path)


def scan_cache(index: SessionIndex) -> dict:
    """Preview-cache usage per session, flagging sessions that no longer exist.

    A full preload leaves ~0.9 GB per 10-minute 3-camera session on disk, so
    caches of deleted sessions are the main way the platform wastes space.
    """
    existing = {s['name'] for s in index.list_sessions()}
    dirs: list[dict] = []
    for croot in cache_roots(index):
        if not croot.is_dir():
            continue
        for d in sorted(croot.iterdir()):
            if not d.is_dir():
                continue
            files, total = _dir_stats(d)
            dirs.append({'session': d.name, 'path': str(d), 'files': files,
                         'mb': round(total / (1024 * 1024), 1),
                         'stale': d.name not in existing})
    stale = [d for d in dirs if d['stale']]
    return {'cache_roots': [str(r) for r in cache_roots(index)],
            'dirs': dirs,
            'stale_count': len(stale),
            'stale_mb': round(sum(d['mb'] for d in stale), 1),
            'total_mb': round(sum(d['mb'] for d in dirs), 1)}


def clean_cache(index: SessionIndex, confirm: bool = False) -> dict:
    """Delete cached previews of sessions that are gone from the active root.

    Only ever removes directories that are direct children of a known cache
    root (a stale name must not be able to reach outside the cache tree).
    Deleting tens of thousands of small files takes a while — the caller shows
    a confirmation before this runs.
    """
    if not confirm:
        raise ValueError('删除缓存需要 confirm=true')
    allowed = [r.resolve() for r in cache_roots(index)]
    existing = {s['name'] for s in index.list_sessions()}
    removed, freed, failed = [], 0, []
    for root in allowed:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name in existing:
                continue
            try:
                if d.resolve().parent != root:
                    failed.append({'path': str(d), 'error': 'not a direct child'})
                    continue
                _files, size = _dir_stats(d)
                shutil.rmtree(d)
                removed.append({'session': d.name, 'mb': round(size / 1048576, 1)})
                freed += size
            except OSError as e:
                failed.append({'path': str(d), 'error': str(e)})
    return {'removed': removed, 'failed': failed,
            'removed_count': len(removed),
            'freed_mb': round(freed / 1048576, 1)}


def _encode_preview_fname(cache_root: Path, session_dir: Path, fname: str,
                          session: str, cam: str, row: int,
                          max_width: int) -> bytes:
    """Decode + downscale + JPEG-encode one row and write it to the disk
    cache. Takes a pre-resolved filename (the preload precomputes all of
    them once instead of paying ~220 us/row of pandas lookups). Bypasses
    the LRU so background preload workers actually run in parallel (the
    LRU holds its lock for the whole wrapped call)."""
    dpath = _preview_path(cache_root, session, cam, row, max_width)
    # half-resolution PNG decode: source sizes are exactly 2x the preview
    # targets (1280->640, 640->320), REDUCED_COLOR_2 yields the target size
    # directly and is ~1.6x faster on the wrist cams (no gain on arm2, but
    # harmless); fall back to a full read when it comes out too small.
    img = cv2.imread(str(session_dir / cam / fname),
                     cv2.IMREAD_REDUCED_COLOR_2)
    if img is None or img.shape[1] < max_width:
        img = cv2.imread(str(session_dir / cam / fname))
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
    # write-through so the session needs to be encoded only once ever;
    # via a temp file + replace, so a crash cannot leave a half-written JPEG
    # in the cache (see _read_cached_jpeg)
    try:
        dpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = dpath.parent / (dpath.name + '.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, dpath)
    except OSError:
        pass
    return data
