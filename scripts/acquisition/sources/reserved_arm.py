"""Reserved arm placeholder source.

This source reserves an arm slot (e.g. arm_4) without producing any data.
It allows the arm mapping to be configured in YAML even when the physical
camera for that arm is not yet connected or implemented.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from ..base_source import DataSource, UnifiedTimestamp
from ..naming import arm_name

logger = logging.getLogger(__name__)


@dataclass
class ReservedArmConfig:
    """Configuration for a reserved arm slot."""
    arm_id: int
    topic_name: str = 'camera/arm_reserved/image_raw'


class ReservedArmSource(DataSource):
    """Placeholder source that occupies an arm id but captures nothing."""

    def __init__(self, config: ReservedArmConfig | None = None,
                 clock: UnifiedTimestamp | None = None,
                 output_queue: Any = None):
        super().__init__(clock=clock, output_queue=output_queue)
        self.config = config or ReservedArmConfig(arm_id=4)

    @property
    def name(self) -> str:
        return arm_name(self.config.arm_id) or self.config.topic_name.replace('/', '_')

    def _acquire_loop(self) -> None:
        logger.info('%s is reserved; no data will be captured.', self.name)
        while not self._stop_event.is_set():
            time.sleep(0.5)

    def get_writer_keys(self) -> list[str]:
        # Reserved arms do not write anything to disk.
        return []

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary.update({
            'topic_name': self.config.topic_name,
            'arm_id': self.config.arm_id,
            'reserved': True,
        })
        return summary
