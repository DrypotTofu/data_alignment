"""Configuration loading and validation for acquisition sessions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .registry import SOURCE_BUILDERS
from .session import SessionConfig


def apply_camera_bindings(
    sources: list[dict[str, Any]],
    bindings: dict[str, Any] | None,
) -> None:
    """Apply top-level arm-to-camera-index bindings to camera sources.

    Source entries may still carry ``camera_index`` directly for backward
    compatibility; when a top-level binding exists it takes precedence.
    """
    if not bindings:
        return
    if not isinstance(bindings, dict):
        raise ValueError('camera_bindings must be a mapping like {arm_1: 2}')

    for arm_key, camera_index in bindings.items():
        if not isinstance(arm_key, str) or not arm_key.startswith('arm_'):
            raise ValueError(f'Invalid camera binding key: {arm_key!r}')
        try:
            arm_id = int(arm_key.removeprefix('arm_'))
        except ValueError as exc:
            raise ValueError(f'Invalid camera binding key: {arm_key!r}') from exc
        if not isinstance(camera_index, int) or camera_index < 0:
            raise ValueError(
                f'camera_bindings.{arm_key} must be a non-negative integer'
            )

        matches = [
            source_cfg for source_cfg in sources
            if isinstance(source_cfg, dict) and source_cfg.get('arm_id') == arm_id
        ]
        if not matches:
            raise ValueError(f'camera_bindings.{arm_key} has no matching source')
        for source_cfg in matches:
            source_cfg['camera_index'] = camera_index


def _source_label(source_cfg: dict[str, Any], index: int) -> str:
    return str(source_cfg.get('name') or source_cfg.get('type') or f'source[{index}]')


def validate_session_config(config: SessionConfig) -> None:
    """Fail fast on ambiguous or contradictory source configuration."""
    errors: list[str] = []
    if not config.sources:
        errors.append('at least one source is required')

    for index, source_cfg in enumerate(config.sources):
        if not isinstance(source_cfg, dict):
            errors.append(f'source[{index}]: source entries must be mappings')

    enabled_sources = [
        (i, s) for i, s in enumerate(config.sources)
        if isinstance(s, dict) and s.get('enabled') is not False
    ]
    if not enabled_sources:
        errors.append('at least one source must be enabled')

    names: set[str] = set()
    arm_ids: dict[int, str] = {}
    camera_indices: dict[int, str] = {}

    for index, source_cfg in enabled_sources:
        label = _source_label(source_cfg, index)
        kind = source_cfg.get('type')
        if kind not in SOURCE_BUILDERS:
            errors.append(f'{label}: unknown source type {kind!r}')

        name = str(source_cfg.get('name') or kind)
        if name in names:
            errors.append(f'{label}: duplicate source name {name!r}')
        names.add(name)

        arm_id = source_cfg.get('arm_id')
        if arm_id is not None:
            if not isinstance(arm_id, int) or not 1 <= arm_id <= 4:
                errors.append(f'{label}: arm_id must be an integer in 1..4')
            elif arm_id in arm_ids:
                errors.append(
                    f'{label}: arm_id {arm_id} is already used by {arm_ids[arm_id]!r}'
                )
            else:
                arm_ids[arm_id] = label

        if kind in {'usb_camera', 'process_camera'}:
            camera_index = source_cfg.get('camera_index')
            if not isinstance(camera_index, int) or camera_index < 0:
                errors.append(f'{label}: camera_index must be a non-negative integer')
            elif camera_index in camera_indices:
                errors.append(
                    f'{label}: camera_index {camera_index} is already used by '
                    f'{camera_indices[camera_index]!r}'
                )
            else:
                camera_indices[camera_index] = label

            for key in ('width', 'height'):
                value = source_cfg.get(key)
                if value is not None and (not isinstance(value, int) or value <= 0):
                    errors.append(f'{label}: {key} must be a positive integer')

            target_fps = source_cfg.get('target_fps')
            if target_fps is not None and target_fps <= 0:
                errors.append(f'{label}: target_fps must be positive')

            png_compression = source_cfg.get('png_compression', 1)
            if not 0 <= png_compression <= 9:
                errors.append(f'{label}: png_compression must be in 0..9')

            writer_threads = source_cfg.get('writer_threads', 1)
            if not isinstance(writer_threads, int) or writer_threads < 1:
                errors.append(f'{label}: writer_threads must be a positive integer')

        if kind == 'twincat':
            selected_arm = source_cfg.get('selected_arm', 1)
            if not isinstance(selected_arm, int) or not 1 <= selected_arm <= 4:
                errors.append(f'{label}: selected_arm must be an integer in 1..4')
            num_connections = source_cfg.get('num_connections', 1)
            if not isinstance(num_connections, int) or num_connections < 1:
                errors.append(f'{label}: num_connections must be a positive integer')

    if errors:
        details = '\n'.join(f'  - {error}' for error in errors)
        raise ValueError(f'Invalid acquisition configuration:\n{details}')


def load_config(path: str | Path) -> SessionConfig:
    """Load and validate a YAML acquisition config."""
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    camera_bindings = data.pop('camera_bindings', None)
    config = SessionConfig(**data)
    apply_camera_bindings(config.sources, camera_bindings)
    validate_session_config(config)
    return config


def read_base_dir(path: str | Path) -> str | None:
    """``base_dir`` value of an acquisition config (None when absent)."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None
    value = data.get('base_dir')
    return None if value is None else str(value)


def set_base_dir(path: str | Path, base_dir: str) -> None:
    """Rewrite the top-level ``base_dir:`` line of an acquisition config.

    Line-level rewrite on purpose: a full yaml.safe_dump would delete every
    comment in these hand-maintained configs (camera bindings, format notes).
    Creates the key right after the leading comment block when missing.

    ``base_dir`` is resolved by the acquisition session relative to the
    process CWD, so an absolute path is what the picker writes.
    """
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.lstrip().startswith('base_dir:'):
            indent = line[:len(line) - len(line.lstrip())]
            eol = '\r\n' if line.endswith('\r\n') else '\n'
            lines[i] = f'{indent}base_dir: {base_dir}{eol}'
            break
    else:
        insert_at = 0
        for i, line in enumerate(lines):        # after the leading comments
            if line.strip() and not line.lstrip().startswith('#'):
                break
            insert_at = i + 1
        lines.insert(insert_at, f'base_dir: {base_dir}\n')
    p.write_text(''.join(lines), encoding='utf-8')


def arm_key(arm_id: int) -> str:
    """``1 -> 'arm_1'`` (the key format of the ``camera_bindings`` mapping)."""
    return f'arm_{int(arm_id)}'


def read_camera_bindings(path: str | Path) -> dict[str, int] | None:
    """Top-level ``camera_bindings`` mapping (None when absent/unreadable)."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None
    bindings = data.get('camera_bindings')
    if not isinstance(bindings, dict):
        return None
    return {str(k): v for k, v in bindings.items()}


def set_camera_bindings(path: str | Path, bindings: dict[str, int]) -> dict:
    """Rewrite ``camera_bindings`` arm->index entries, preserving everything else.

    Same line-level approach as :func:`set_base_dir` (a ``yaml.safe_dump`` would
    erase the comments these configs rely on). Only the ``arm_N: idx`` lines
    inside the ``camera_bindings`` block are touched; missing arms are appended
    to that block (or the block is created after ``base_dir``); unrelated keys
    and comments inside the block are left alone.

    Returns ``{'updated': [...], 'added': [...], 'path': ...}``.
    """
    p = Path(path)
    lines = p.read_text(encoding='utf-8').splitlines(keepends=True)
    wanted = {str(k): int(v) for k, v in bindings.items()}

    start = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith('camera_bindings:') and not line[:1].isspace():
            start = i
            break

    updated: list[str] = []
    added: list[str] = []
    if start is None:
        insert_at = 0
        for i, line in enumerate(lines):        # after base_dir / comments
            if line.lstrip().startswith('base_dir:'):
                insert_at = i + 1
                break
        else:
            for i, line in enumerate(lines):
                if line.strip() and not line.lstrip().startswith('#'):
                    break
                insert_at = i + 1
        block = ['camera_bindings:\n'] + [
            f'  {k}: {wanted[k]}\n' for k in sorted(wanted, key=_arm_sort_key)]
        lines[insert_at:insert_at] = block
        added = sorted(wanted, key=_arm_sort_key)
    else:
        end = start + 1
        while end < len(lines):
            line = lines[end]
            if line.strip() and not line[:1].isspace():
                break                            # next top-level key
            end += 1
        indent = '  '
        last_child = start
        for i in range(start + 1, end):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key = line.split(':', 1)[0].strip()
            last_child = i
            if key in wanted:
                lead = line[:len(line) - len(line.lstrip())]
                eol = '\r\n' if line.endswith('\r\n') else '\n'
                lines[i] = f'{lead}{key}: {wanted.pop(key)}{eol}'
                updated.append(key)
            elif line[:1].isspace():
                indent = line[:len(line) - len(line.lstrip())] or indent
        missing = sorted(wanted, key=_arm_sort_key)
        lines[last_child + 1:last_child + 1] = [
            f'{indent}{k}: {wanted[k]}\n' for k in missing]
        added = missing
    p.write_text(''.join(lines), encoding='utf-8')
    return {'updated': updated, 'added': added, 'path': str(p)}


def _arm_sort_key(key: str) -> tuple[int, str]:
    try:
        return (int(str(key).removeprefix('arm_')), key)
    except ValueError:
        return (99, key)
