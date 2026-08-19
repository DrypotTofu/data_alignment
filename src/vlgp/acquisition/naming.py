"""Naming helpers for sources, arms, and output directories."""
from __future__ import annotations

ARM_PREFIX = 'camera_arm'


def arm_name(arm_id: int | None) -> str | None:
    """Return the canonical arm name, e.g. camera_arm_2."""
    if arm_id is None:
        return None
    return f'{ARM_PREFIX}_{arm_id}'


def source_output_name(topic_name: str, arm_id: int | None) -> str:
    """Return the output/directory name for a source.

    If an arm_id is configured, use ``camera_arm_<id>``; otherwise fall back
    to the sanitized topic name.
    """
    return arm_name(arm_id) or topic_name.replace('/', '_')
