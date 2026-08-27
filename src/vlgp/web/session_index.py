"""Session data access layer for the Web platform (T15 v0).

Reads session layout / alignment tables / annotations from disk using the
canonical vlgp layout helpers, with lightweight caching. All paths are
resolved relative to the repo ``sessions/`` root and validated (no path
traversal).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from vlgp.acquisition import session_layout as sl

REPO_ROOT = Path(__file__).resolve().parents[3]
SESSIONS_ROOT = REPO_ROOT / 'sessions'

CAMERA_ORDER = ('camera_arm_2', 'camera_arm_1', 'camera_arm_3')
FILENAME_COLS = {'camera_arm_2': 'anchor_filename',
                 'camera_arm_1': 'camera_arm_1_filename',
                 'camera_arm_3': 'camera_arm_3_filename'}
ARMS = (1, 2, 3, 4)

DEFAULT_ANNOTATIONS = {
    'version': 1,
    'reviewed_by': '',
    'reviewed_at': '',
    'status': 'pending',
    'reject_reason': '',
    'trim': None,
    'segments': [],
    'flags': [],
    'notes': '',
}


def _safe_name(name: str) -> str:
    """Reject path separators / traversal in session names."""
    base = os.path.basename(name)
    if base != name or base in ('', '.', '..'):
        raise ValueError(f'invalid session name: {name!r}')
    return base


class SessionNotFound(FileNotFoundError):
    pass


class NoAlignment(FileNotFoundError):
    pass


class SessionIndex:
    """Per-process session discovery + cached alignment access."""

    def __init__(self, sessions_root: Path | None = None):
        self.root = Path(sessions_root) if sessions_root else SESSIONS_ROOT
        self._align_cache: dict[str, tuple[float, pd.DataFrame]] = {}

    # ------------------------------------------------------------------ paths
    def session_dir(self, name: str) -> Path:
        name = _safe_name(name)
        d = self.root / name
        if not d.is_dir():
            raise SessionNotFound(f'session not found: {d}')
        return d

    def list_sessions(self) -> list[dict]:
        out = []
        if not self.root.is_dir():
            return out
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            try:
                info = self.session_info(d.name)
            except (NoAlignment, OSError):
                continue
            out.append(info)
        return out

    def session_info(self, name: str) -> dict:
        d = self.session_dir(name)
        align_path = sl.alignment_path(d)
        info = {
            'name': d.name,
            'has_alignment': align_path.exists(),
            'n_rows': 0,
            'cameras': {c: (d / c).is_dir() for c in CAMERA_ORDER},
            'annotations': self.read_annotations(d.name),
        }
        if align_path.exists():
            info['n_rows'] = len(self.alignment(name))
        return info

    # ------------------------------------------------------------- alignment
    def alignment(self, name: str) -> pd.DataFrame:
        """Alignment table for a session (cached; invalidated on mtime change)."""
        d = self.session_dir(name)
        p = sl.alignment_path(d)
        if not p.exists():
            raise NoAlignment(f'no alignment table: {p}')
        mtime = p.stat().st_mtime
        hit = self._align_cache.get(name)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        df = pd.read_csv(p)
        self._align_cache[name] = (mtime, df)
        return df

    def frame_filename(self, name: str, cam: str, row: int) -> str:
        if cam not in FILENAME_COLS:
            raise ValueError(f'unknown camera: {cam}')
        df = self.alignment(name)
        if not 0 <= row < len(df):
            raise IndexError(f'row {row} out of range [0, {len(df)})')
        fname = df.iloc[row][FILENAME_COLS[cam]]
        if not isinstance(fname, str) or not fname:
            raise FileNotFoundError(f'{name}/{cam}: no frame at row {row}')
        if Path(fname).name != fname:
            raise ValueError(f'unsafe filename {fname!r}')
        return fname

    def invalidate(self, name: str) -> None:
        """Drop cached alignment data for one session (after regeneration)."""
        self._align_cache.pop(_safe_name(name), None)

    # ------------------------------------------------------- row status
    def row_status(self, name: str, row: int) -> dict:
        """Per-arm robot status snapshot for one alignment row.

        NaN fields (pose-invalid rows) become None. Includes the instrument
        label via vlgp's instrument table.
        """
        from vlgp.dataset.build_srth_dataset import instrument_label
        df = self.alignment(name)
        if not 0 <= row < len(df):
            raise IndexError(f'row {row} out of range [0, {len(df)})')
        r = df.iloc[row]
        prev = df.iloc[row - 1] if row > 0 else None

        def g(col: str):
            v = r.get(col)
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if f != f else round(f, 4)  # NaN -> None

        arms: dict[str, dict] = {}
        for a in ARMS:
            pre = f'arm{a}_'
            pos = [g(pre + c) for c in ('position_x', 'position_y', 'position_z')]
            scope = [g(pre + c) for c in ('scope_position_x', 'scope_position_y',
                                          'scope_position_z')]
            quat = [g(pre + c) for c in ('orientation_w', 'orientation_x',
                                         'orientation_y', 'orientation_z')]
            inst = g(pre + 'instrument_type')
            entry = {
                'position_mm': pos,
                'scope_position_mm': scope,
                'orientation_wxyz': quat,
                'gripper_angle': g(pre + 'gripper_angle'),
                'instrument_type': inst,
                'instrument': instrument_label(inst) if inst is not None else None,
                'energy_instrument_status': g(pre + 'energy_instrument_status'),
            }
            entry['dpos_mm'] = None
            if prev is not None and all(v is not None for v in scope):
                dv: list[float] = []
                ok = True
                for k, c in enumerate(('scope_position_x', 'scope_position_y',
                                       'scope_position_z')):
                    pv = prev.get(pre + c)
                    if pv is None:
                        ok = False
                        break
                    try:
                        pf = float(pv)
                    except (TypeError, ValueError):
                        ok = False
                        break
                    if pf != pf:  # NaN
                        ok = False
                        break
                    dv.append(scope[k] - pf)
                if ok:
                    entry['dpos_mm'] = round(
                        float(np.sqrt(sum(x * x for x in dv))), 4)
            arms[f'arm{a}'] = entry
        return {'row': int(row), 'arms': arms}

    # -------------------------------------------------------------- motion
    def motion(self, name: str, stride: int | None = None,
               points: int = 2000) -> dict:
        """Compact motion-curve data (|dpos| per arm + arm1 gripper_angle).

        Mirrors the PyQt5 review tool: |dpos| = norm of the scope-position
        step between consecutive alignment rows; rows are the original
        0-based alignment indices.

        Downsampling: if ``stride`` is given it wins; otherwise a stride is
        derived so the payload stays <= ``points`` samples per series (long
        sessions stay light while short sessions keep full resolution).
        """
        df = self.alignment(name)
        n = len(df)
        if stride is None:
            stride = max(1, int(np.ceil(n / max(1, int(points)))))
        else:
            stride = max(1, int(stride))
        rows = np.arange(0, n, stride)
        payload: dict = {
            'n_rows': n,
            'stride': stride,
            'rows': rows.tolist(),
            'dpos': {},
            'gripper': {},
        }
        for a in ARMS:
            col = f'arm{a}_scope_position_x'
            if col not in df.columns:
                continue
            dx = df[f'arm{a}_scope_position_x'].to_numpy(dtype=float)
            dy = df[f'arm{a}_scope_position_y'].to_numpy(dtype=float)
            dz = df[f'arm{a}_scope_position_z'].to_numpy(dtype=float)
            d = np.zeros(n)
            d[1:] = np.sqrt((dx[1:] - dx[:-1]) ** 2
                            + (dy[1:] - dy[:-1]) ** 2
                            + (dz[1:] - dz[:-1]) ** 2)
            # rows without valid pose carry NaN scope positions -> sanitise
            # (PyQt5 _redraw_curve uses nan_to_num as well); NaN breaks JSON.
            d = np.nan_to_num(d, nan=0.0, posinf=0.0, neginf=0.0)
            payload['dpos'][f'arm{a}'] = np.round(d[rows], 4).tolist()
        for a in (1,):
            col = f'arm{a}_gripper_angle'
            if col in df.columns:
                g = np.nan_to_num(df[col].to_numpy(dtype=float),
                                  nan=0.0, posinf=0.0, neginf=0.0)
                payload['gripper'][f'arm{a}'] = np.round(g[rows], 3).tolist()
        return payload

    # --------------------------------------------------------- annotations
    def read_annotations(self, name: str) -> dict:
        p = sl.annotations_path(self.session_dir(name))
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
        return dict(DEFAULT_ANNOTATIONS)

    def write_annotations(self, name: str, ann: dict) -> None:
        d = self.session_dir(name)
        p = sl.annotations_path(d)
        p.write_text(json.dumps(ann, ensure_ascii=False, indent=2),
                     encoding='utf-8')

    # ------------------------------------------------- review manifest
    def manifest_path(self) -> Path:
        """sessions/review_manifest.json — batch queue (PyQt5 parity)."""
        return self.root / 'review_manifest.json'

    def read_manifest(self) -> dict:
        p = self.manifest_path()
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
        return {}

    def update_manifest(self, name: str, status: str, reviewed_at: str) -> None:
        """Mirror the PyQt5 review tool's _update_manifest (same key order)."""
        manifest = self.read_manifest()
        manifest[name] = {'status': status, 'reviewed_at': reviewed_at,
                          'has_annotations': True}
        p = self.manifest_path()
        p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                     encoding='utf-8')

    def review_queue(self) -> dict:
        """Pending sessions (manifest status not approved/rejected), PyQt5 parity.

        PyQt5's batch mode (collect_sessions) only queues sessions WITH an
        alignment CSV — sessions without one cannot be reviewed and are
        excluded from the queue.
        """
        manifest = self.read_manifest()
        sessions = [s for s in self.list_sessions() if s.get('has_alignment')]
        pending = [s['name'] for s in sessions
                   if manifest.get(s['name'], {}).get('status')
                   not in ('approved', 'rejected')]
        return {'pending': pending, 'n_pending': len(pending),
                'n_total': len(sessions)}
