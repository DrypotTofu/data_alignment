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

        if kind == 'usb_multi_camera':
            configs = source_cfg.get('configs', [])
            if not isinstance(configs, list) or not configs:
                errors.append(f'{label}: configs must be a non-empty list')
            else:
                multi_indices: set[int] = set()
                for camera_cfg in configs:
                    if not isinstance(camera_cfg, dict):
                        errors.append(f'{label}: nested camera configs must be mappings')
                        continue
                    camera_index = camera_cfg.get('camera_index')
                    if not isinstance(camera_index, int) or camera_index < 0:
                        errors.append(f'{label}: nested camera_index must be a non-negative integer')
                    elif camera_index in multi_indices:
                        errors.append(f'{label}: duplicate nested camera_index {camera_index}')
                    else:
                        multi_indices.add(camera_index)

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
