"""Session deletion with a recoverable-by-default trash (Web platform).

Deleting a session removes raw acquisition data (a single session is easily
1-150 GB and cannot be re-created without the rig), so the Web default is a
**move into the data root's trash** (``<root>/_trash/<name>_<timestamp>``):

* same-volume rename → instant even for 150 GB, and reversible;
* ``_``-prefixed, so it never shows up as a session;
* emptied explicitly with :func:`empty_trash` (that is the step that frees the
  space) or bypassed with ``permanent=True`` when the space is needed now.

Everything validates through :meth:`SessionIndex.session_dir`, so a name can
never escape the active data root, and the per-session preview cache is dropped
together with the session (it would otherwise linger as stale junk).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from vlgp.web.fs_browse import dir_stats

TRASH_DIRNAME = '_trash'


def trash_root(index) -> Path:
    return index.root / TRASH_DIRNAME


def session_summary(index, name: str) -> dict:
    """What the delete confirmation dialog shows (size + content + review state)."""
    d = index.session_dir(name)
    files, total = dir_stats(d)
    info = index.session_info(name)
    cameras = {c: ok for c, ok in (info.get('cameras') or {}).items() if ok}
    ann = info.get('annotations') or {}
    return {
        'name': name, 'path': str(d),
        'files': files, 'bytes': total, 'mb': round(total / 1048576, 1),
        'gb': round(total / 1073741824, 2),
        'n_rows': info.get('n_rows', 0),
        'cameras': cameras, 'has_alignment': info.get('has_alignment', False),
        'status': ann.get('status', 'pending'),
        'trim': ann.get('trim'),
        'recording': False,
    }


def _check_not_busy(index, name: str, pipeline=None, preload=None,
                    acquisition=None) -> None:
    """Refuse to delete a session that something is still using."""
    from vlgp.web.acquisition_service import _acquisition
    from vlgp.web.align_service import alignment_status
    from vlgp.web.frame_service import PreloadRegistry
    from vlgp.web.pipeline_service import _pipeline

    acquisition = acquisition or _acquisition
    if acquisition.running():
        st = acquisition.status()
        raise ValueError(
            f'正在录制（{st.get("session")}），删除会话前请先停止录制')
    pipeline = pipeline or _pipeline
    running = pipeline.running_jobs()
    if running:
        raise ValueError('有流水线任务在运行（'
                         + '；'.join(j['label'] for j in running)
                         + '），请等待完成后再删除')
    align = alignment_status(name)
    if align.get('status') == 'running':
        raise ValueError(f'{name} 正在生成对齐表，请等待完成后再删除')


def delete_session(index, name: str, permanent: bool = False,
                   confirm: bool = False) -> dict:
    """Delete a session: to the trash by default, permanently on request.

    ``confirm`` must be true — the API always passes it after the UI showed the
    operator what is about to be removed.
    """
    if not confirm:
        raise ValueError('删除会话需要 confirm=true')
    d = index.session_dir(name)          # validates the name / root
    _check_not_busy(index, name)

    summary = session_summary(index, name)
    # the preview cache belongs to this session: drop it either way
    cache_removed = 0
    for croot in _preview_roots(index):
        cdir = croot / name
        if cdir.is_dir():
            shutil.rmtree(cdir, ignore_errors=True)
            cache_removed += 1

    if permanent:
        shutil.rmtree(d)
        index.invalidate(name)
        return {**summary, 'mode': 'permanent', 'trashed_to': None,
                'cache_dirs_removed': cache_removed}

    dest = trash_root(index) / f'{name}_{time.strftime("%Y%m%d_%H%M%S")}'
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(dest.name + f'_{int(time.time() * 1000) % 1000:03d}')
    shutil.move(str(d), str(dest))
    index.invalidate(name)
    return {**summary, 'mode': 'trash', 'trashed_to': str(dest),
            'trash_entry': dest.name, 'cache_dirs_removed': cache_removed}


def _preview_roots(index) -> list[Path]:
    from vlgp.web.frame_service import cache_roots
    return [r for r in cache_roots(index) if r.is_dir()]


def trash_entries(index) -> dict:
    """Trashed sessions with their sizes (the space they still occupy)."""
    root = trash_root(index)
    entries = []
    if root.is_dir():
        for d in sorted(root.iterdir(), key=lambda p: p.name):
            if not d.is_dir():
                continue
            try:
                st = d.stat()
            except OSError:
                continue
            entries.append({'entry': d.name, 'path': str(d),
                            'mtime': st.st_mtime})
    for e in entries:
        files, total = dir_stats(Path(e['path']))
        e['files'] = files
        e['mb'] = round(total / 1048576, 1)
        e['gb'] = round(total / 1073741824, 2)
    return {'trash_dir': str(root), 'exists': root.is_dir(),
            'entries': entries,
            'total_gb': round(sum(e['gb'] for e in entries), 2),
            'count': len(entries)}


def restore_from_trash(index, entry: str) -> dict:
    """Move a trashed session back to the data root (refuses name clashes)."""
    root = trash_root(index)
    src = (root / entry)
    if not src.is_dir() or src.parent.resolve() != root.resolve():
        raise ValueError(f'回收站里没有 {entry!r}')
    # entry is "<session>_<YYYYmmdd_HHMMSS>": strip the trailing stamp
    name = entry
    parts = entry.rsplit('_', 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        name = parts[0]
    dest = index.root / name
    if dest.exists():
        raise ValueError(f'{dest} 已存在，无法恢复（请先改名或删除现有目录）')
    shutil.move(str(src), str(dest))
    index.invalidate(name)
    return {'restored': str(dest), 'name': name, 'entry': entry}


def empty_trash(index, confirm: bool = False) -> dict:
    """Permanently delete every trashed session (this is what frees space)."""
    if not confirm:
        raise ValueError('清空回收站需要 confirm=true')
    root = trash_root(index)
    removed: list[dict] = []
    freed = 0
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            files, total = dir_stats(d)
            try:
                shutil.rmtree(d)
            except OSError as e:
                removed.append({'entry': d.name, 'error': str(e)})
                continue
            freed += total
            removed.append({'entry': d.name, 'mb': round(total / 1048576, 1),
                            'files': files})
    return {'removed': removed, 'removed_count': len(removed),
            'freed_gb': round(freed / 1073741824, 2),
            'freed_mb': round(freed / 1048576, 1)}
