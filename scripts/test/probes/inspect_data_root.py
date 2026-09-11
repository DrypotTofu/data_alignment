# -*- coding: utf-8 -*-
"""Read-only inspection of an external data root (E:\\sessions backup).

Answers, without writing anything into the inspected folder:
  1) how expensive is `SessionIndex.list_sessions()` (it parses every alignment
     CSV with pandas just to report n_rows + trim status);
  2) do the real 3-camera sessions carry the per-camera filename columns that
     IN/OUT video export maps through, and are the derived per-camera seconds
     different from the grid-relative fallback (i.e. is the mapping real).

Usage:  python scripts/test/probes/inspect_data_root.py E:\\sessions
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from vlgp.acquisition import session_layout as sl
from vlgp.web.pipeline_service import video_trim_args
from vlgp.web.session_index import SessionIndex, row_count


def fast_row_count(csv_path: Path) -> int:
    """Data rows by counting newlines (no pandas) — cross-checked vs pandas."""
    return row_count(csv_path)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else r'E:\sessions')
    print(f'root: {root}  exists={root.is_dir()}')
    dirs = sorted(d for d in root.iterdir() if d.is_dir()
                  and not d.name.startswith(('_', '.')))
    print(f'{len(dirs)} candidate session folders')

    print('\n--- per-session (read-only) ---')
    for d in dirs:
        align = sl.alignment_path(d)
        cams = [c.name for c in sorted(d.glob('camera_arm_*')) if c.is_dir()]
        with_meta = [c for c in cams if (d / c / 'metadata.csv').exists()]
        rows = None
        t0 = time.perf_counter()
        if align.exists():
            rows = fast_row_count(align)
        dt = time.perf_counter() - t0
        print(f'  {d.name}: align={align.exists()} rows={rows} '
              f'cameras={len(with_meta)}/{len(cams)} (count {dt * 1000:.0f} ms)')

    idx = SessionIndex(root)
    t0 = time.perf_counter()
    sessions = idx.list_sessions()
    dt = time.perf_counter() - t0
    print(f'\nlist_sessions(): {len(sessions)} sessions in {dt:.2f} s'
          f' ({dt / max(1, len(sessions)) * 1000:.0f} ms/session, no pandas)')

    print('\n--- row_count() vs pandas len(df) ---')
    bad = 0
    for d in dirs:
        align = sl.alignment_path(d)
        if not align.exists():
            continue
        fast = row_count(align)
        exact = len(idx.alignment(d.name))
        ok = fast == exact
        bad += 0 if ok else 1
        print(f'  [{"PASS" if ok else "FAIL"}] {d.name}: count={fast} pandas={exact}')
    print(f'  row_count mismatches: {bad}')

    print('\n--- IN/OUT export mapping on real 3-camera data ---')
    for s in sessions:
        name = s['name']
        if not s['has_alignment'] or not all(s['cameras'].values()):
            print(f'  {name}: skipped (align={s["has_alignment"]}, '
                  f'cameras={s["cameras"]})')
            continue
        df = idx.alignment(name)
        cols = [c for c in ('anchor_filename', 'camera_arm_1_filename',
                            'camera_arm_3_filename') if c in df.columns]
        n = len(df)
        # an in-range trim: the API validates 0 <= start <= end < n_rows
        trim = {'start_row': min(n // 4, n - 2), 'end_row': min(n // 4 + 100, n - 1)}
        args = video_trim_args(idx, name, idx.session_dir(name), trim)
        grid = trim['start_row'] / 30.0
        print(f'  {name}: rows={n} filename_cols={len(cols)}/3')
        for cam, a in args.items():
            d = a['start_sec'] - grid
            print(f'    {cam}: start={a["start_sec"]:.4f} '
                  f'(grid {grid:.4f}, diff {d * 1000:+.1f} ms) source={a["source"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
