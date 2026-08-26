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

    # -------------------------------------------------------------- motion
    def motion(self, name: str, stride: int = 1) -> dict:
        """Compact motion-curve data (|dpos| per arm + arm1 gripper_angle).

        Mirrors the PyQt5 review tool: |dpos| = norm of the scope-position
        step between consecutive alignment rows; rows are the original
        0-based alignment indices (optionally strided).
        """
        stride = max(1, int(stride))
        df = self.alignment(name)
        rows = np.arange(0, len(df), stride)
        payload: dict = {
            'n_rows': int(len(df)),
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
            d = np.zeros(len(df))
            d[1:] = np.sqrt((dx[1:] - dx[:-1]) ** 2
                            + (dy[1:] - dy[:-1]) ** 2
                            + (dz[1:] - dz[:-1]) ** 2)
            payload['dpos'][f'arm{a}'] = np.round(d[rows], 4).tolist()
        for a in (1,):
            col = f'arm{a}_gripper_angle'
            if col in df.columns:
                payload['gripper'][f'arm{a}'] = np.round(
                    df[col].to_numpy(dtype=float)[rows], 3).tolist()
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
