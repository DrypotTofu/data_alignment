# -*- coding: utf-8 -*-
"""Delete regression-test leftovers (annotations.json) from a data root.

Background: `web_regression.py` reviewed EVERY pending session and only restored
the annotations of the session it picked, so a run left the other sessions with
a bogus `status=rejected, trim={0,0}, notes="regression"` file. Those files make
`build_srth_dataset` silently SKIP the session. The suite restores everything
now, but old roots (e.g. the E:\\sessions backup) still carry the leftovers.

Safety: a file is deleted only when it matches the full regression signature —
anything else (a real review decision) is reported and left alone.

Usage:
    python scripts/test/probes/clean_stale_annotations.py --root E:\\sessions            # dry run
    python scripts/test/probes/clean_stale_annotations.py --root E:\\sessions --apply
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SIGNATURE = {
    'status': 'rejected',
    'reject_reason': 'rejected in review',
    'notes': 'regression',
    'trim': {'start_row': 0, 'end_row': 0},
    'segments': [],
    'flags': [],
}


def is_regression_leftover(ann: dict) -> bool:
    return all(ann.get(k) == v for k, v in SIGNATURE.items())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=Path(r'E:\sessions'))
    ap.add_argument('--apply', action='store_true',
                    help='actually delete (default: dry run)')
    args = ap.parse_args()
    root: Path = args.root
    if not root.is_dir():
        raise SystemExit(f'not a directory: {root}')

    doomed: list[Path] = []
    keep: list[tuple[Path, str]] = []
    print(f'root: {root}   mode: {"APPLY" if args.apply else "dry run"}')
    for d in sorted(p for p in root.iterdir() if p.is_dir()
                    and not p.name.startswith(('_', '.'))):
        p = d / 'annotations.json'
        if not p.exists():
            continue
        try:
            ann = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            keep.append((p, f'unreadable: {e}'))
            continue
        who = f"{ann.get('reviewed_by', '')} @ {ann.get('reviewed_at', '')}"
        if is_regression_leftover(ann):
            doomed.append(p)
            print(f'  DELETE {d.name}: regression leftover ({who})')
        else:
            keep.append((p, f"status={ann.get('status')} notes={ann.get('notes')!r} "
                            f"trim={ann.get('trim')} reviewed_by={who}"))
            print(f'  keep   {d.name}: {keep[-1][1]}')

    print(f'\nregression leftovers: {len(doomed)}   real/other annotations: {len(keep)}')
    if args.apply:
        for p in doomed:
            p.unlink()
        print(f'deleted {len(doomed)} files')
    elif doomed:
        print('dry run: nothing deleted (add --apply)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
