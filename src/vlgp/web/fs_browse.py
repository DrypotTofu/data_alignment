"""Server-side filesystem browsing for the data-root picker (T15 extension).

The browser cannot open a native folder dialog on the server, so the picker
is rendered by the frontend from these endpoints:

  list_drives()            -> volumes with capacity/free space
  list_dir(path)           -> sub-directories (+ session markers)
  disk_usage(path)         -> total/used/free for one path
  is_writable(path)        -> can the platform write caches/annotations here
  reveal(path)             -> open the folder in the OS file manager

Only directory NAMES are ever returned — no file contents are exposed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from vlgp.acquisition import session_layout as sl

# A candidate data root is "big enough to scan" below this many children;
# above it we skip the session count (C:\Windows, Program Files, ...).
COUNT_CHILD_CAP = 400


def disk_usage(path: str | Path) -> dict | None:
    """{'total_gb','used_gb','free_gb','free_pct'} or None when unavailable."""
    import shutil
    try:
        u = shutil.disk_usage(str(path))
    except OSError:
        return None
    gb = 1024 ** 3
    return {'total_gb': round(u.total / gb, 1),
            'used_gb': round(u.used / gb, 1),
            'free_gb': round(u.free / gb, 1),
            'free_pct': round(100.0 * u.free / u.total, 1) if u.total else 0.0}


def is_writable(path: str | Path) -> bool:
    p = Path(path)
    if not p.is_dir():
        return False
    if os.access(str(p), os.W_OK):
        return True
    probe = p / '.vlgp_write_probe'
    try:
        probe.write_bytes(b'')
        probe.unlink()
        return True
    except OSError:
        return False


def list_drives() -> list[dict]:
    """Volumes to offer as picker shortcuts (Windows drive letters, POSIX /)."""
    out: list[dict] = []
    if sys.platform == 'win32':
        for letter in _windows_drives():
            root = f'{letter}:\\'
            if not os.path.isdir(root):
                continue
            out.append({'path': root, 'label': f'{letter}:',
                        'disk': disk_usage(root)})
        return out
    out.append({'path': '/', 'label': '/', 'disk': disk_usage('/')})
    home = Path.home()
    if str(home) != '/':
        out.append({'path': str(home), 'label': f'~ ({home.name})',
                    'disk': disk_usage(home)})
    return out


def _windows_drives() -> list[str]:
    """Bitmask of mounted logical drives (falls back to probing A:-Z:)."""
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [chr(ord('A') + i) for i in range(26) if mask & (1 << i)]
    except Exception:  # noqa: BLE001 — non-Windows/odd env
        return [chr(ord('A') + i) for i in range(26)
                if os.path.isdir(f'{chr(ord("A") + i)}:\\')]


def looks_like_session(p: Path) -> bool:
    return sl.alignment_path(p).exists()


def dir_stats(path: str | Path) -> tuple[int, int]:
    """(files, bytes) under ``path`` via os.scandir.

    DirEntry.stat() is served from the Windows directory enumeration, so this
    costs no syscall per file: 367k cached JPEGs measure in ~0.8 s against
    ~107 s for os.walk + os.path.getsize (measured 2026-09-11). Shared by the
    preview-cache scanner and the session-delete summary.
    """
    files = total = 0
    stack = [Path(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            files += 1
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return files, total


def count_sessions(p: Path) -> int | None:
    """Number of session-like children; None when the folder is too big to
    scan cheaply (the picker then shows no count)."""
    try:
        kids = [d for d in p.iterdir() if d.is_dir()]
    except OSError:
        return None
    if len(kids) > COUNT_CHILD_CAP:
        return None
    return sum(1 for d in kids if looks_like_session(d))


def list_dir(path: str | Path, include_files: bool = False) -> dict:
    """Directories under ``path`` with picker annotations.

    Raises ValueError when the path is not a readable directory; the API maps
    that to 422 so the frontend can show the reason instead of failing blank.
    """
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        raise ValueError(f'无法解析路径 {path!r}: {e}') from e
    if not p.is_dir():
        raise ValueError(f'目录不存在或不是文件夹: {p}')

    entries: list[dict] = []
    try:
        children = sorted(p.iterdir(), key=lambda d: d.name.lower())
    except OSError as e:
        raise ValueError(f'无法读取目录 {p}: {e}') from e
    for d in children:
        try:
            is_dir = d.is_dir()
        except OSError:
            continue
        if not is_dir:
            if include_files:
                entries.append({'name': d.name, 'path': str(d), 'type': 'file'})
            continue
        if d.name.startswith('_') or d.name.startswith('.'):
            continue                     # _web_preview / _web_logs / dot dirs
        entry = {'name': d.name, 'path': str(d), 'type': 'dir',
                 'is_session': looks_like_session(d)}
        if not entry['is_session']:
            entry['n_sessions'] = count_sessions(d)
        entries.append(entry)

    parent = p.parent if p.parent != p else None
    return {
        'path': str(p),
        'parent': str(parent) if parent else None,
        'writable': is_writable(p),
        'is_session': looks_like_session(p),
        'n_sessions': count_sessions(p),
        'disk': disk_usage(p),
        'entries': entries,
    }


def reveal(path: str | Path) -> None:
    """Open ``path`` in the OS file manager (no-op on unknown platforms)."""
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f'目录不存在: {p}')
    if sys.platform == 'win32':
        os.startfile(str(p))                     # noqa: S606 — intentional
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(p)])
    else:
        subprocess.Popen(['xdg-open', str(p)])
