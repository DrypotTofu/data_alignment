# -*- coding: utf-8 -*-
"""Timing probe: preview-cache size scan (os.walk+getsize vs os.scandir).

The /api/settings/cache endpoint timed out during the root-picker probe because
the legacy cache under var/web_preview holds ~370k small JPEGs; getsize() costs
a full stat syscall per file while DirEntry.stat() on Windows is served from the
directory enumeration (no extra syscall).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def walk_getsize(root: Path) -> tuple[int, int]:
    files = total = 0
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            files += 1
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return files, total


def scandir_walk(root: Path) -> tuple[int, int]:
    files = total = 0
    stack = [root]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        files += 1
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    return files, total


def count_only(root: Path) -> tuple[int, int]:
    files = 0
    stack = [root]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    files += 1
    return files, 0


def main() -> int:
    roots = [p for p in (REPO / 'var' / 'web_preview',
                         REPO / 'sessions' / '_web_preview') if p.is_dir()]
    for root in roots:
        print(f'--- {root}')
        for name, fn in (('walk+getsize', walk_getsize),
                         ('scandir+stat', scandir_walk),
                         ('scandir count', count_only)):
            t0 = time.perf_counter()
            files, total = fn(root)
            dt = time.perf_counter() - t0
            print(f'  {name:>14}: {files:>7} files  {total / 1048576:>8.1f} MB'
                  f'  {dt:>6.2f} s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
