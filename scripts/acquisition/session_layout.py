"""Canonical filesystem layout helpers for recorded sessions."""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_CAMERA_NAMES = ('camera_arm_1', 'camera_arm_2', 'camera_arm_3')
_ARM_DIR_RE = re.compile(r'robot_pose_arm(?P<arm_id>\d+)$')


def camera_metadata_path(session_dir: Path, camera_name: str) -> Path:
    """Return the metadata path for one camera directory."""
    return Path(session_dir) / camera_name / 'metadata.csv'


def iter_camera_metadata(
    session_dir: Path,
    camera_names: tuple[str, ...] = DEFAULT_CAMERA_NAMES,
) -> dict[str, Path]:
    """Return existing camera metadata paths keyed by camera directory name."""
    result: dict[str, Path] = {}
    for name in camera_names:
        path = camera_metadata_path(session_dir, name)
        if path.exists():
            result[name] = path
    return result


def robot_pose_root(session_dir: Path) -> Path:
    return Path(session_dir) / 'robot_pose'


def per_arm_robot_pose_path(session_dir: Path, arm_id: int) -> Path:
    return robot_pose_root(session_dir) / f'robot_pose_arm{arm_id}' / 'twincat_poses.csv'


def find_per_arm_robot_pose_csvs(session_dir: Path) -> dict[int, Path]:
    """Return current multi-arm robot CSVs keyed by arm id."""
    result: dict[int, Path] = {}
    root = robot_pose_root(session_dir)
    if not root.exists():
        return result
    for path in sorted(root.glob('robot_pose_arm*/twincat_poses.csv')):
        match = _ARM_DIR_RE.match(path.parent.name)
        if match:
            result[int(match.group('arm_id'))] = path
    return result


def legacy_robot_pose_path(session_dir: Path, require_non_empty: bool = True) -> Path | None:
    """Return the legacy single robot CSV if it exists and is usable."""
    path = robot_pose_root(session_dir) / 'twincat_poses.csv'
    if not path.exists():
        return None
    if require_non_empty and path.stat().st_size == 0:
        return None
    return path


def resolve_robot_pose_csv(session_dir: Path, arm_id: int) -> Path | None:
    """Resolve the pose CSV for one arm, preferring the current multi-arm layout."""
    per_arm = per_arm_robot_pose_path(session_dir, arm_id)
    if per_arm.exists():
        return per_arm
    return legacy_robot_pose_path(session_dir)
