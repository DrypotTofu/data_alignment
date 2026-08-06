"""Endoscope acquisition source placeholder.

This module defines the interface expected by the Session framework. The
actual implementation depends on the Hikvision integration path:

  1. Ethernet + Hikvision SDK (MVS/HCNetSDK)  -- preferred
  2. HDMI-out + USB3.0 capture card
  3. RTSP stream from the endoscope host

Once the hardware path is confirmed, replace the stub with the real driver.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ...base_source import DataSample, DataSource
from ...naming import source_output_name
from ...timestamp import UnifiedTimestamp

logger = logging.getLogger(__name__)


class EndoscopeMode:
    SDK = 'sdk'
    HDMI = 'hdmi'
    RTSP = 'rtsp'


@dataclass
class EndoscopeConfig:
    """Configuration for the endoscope source."""
    topic_name: str = 'camera/endoscope/image_raw'
    arm_id: int | None = None  # if set, output name becomes camera_arm_<id>
    mode: str = EndoscopeMode.SDK
    # SDK-specific fields
    ip: str | None = None
    username: str | None = None
    password: str | None = None
    # RTSP/HDMI-specific fields
    url_or_index: str | int | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None


class EndoscopeSource(DataSource):
    """Stub endoscope source that logs a warning and produces no data.

    The design intentionally mirrors `USBCameraSource` so that replacing it
    with a real implementation is straightforward.
    """

    def __init__(self, config: EndoscopeConfig | None = None,
                 clock: UnifiedTimestamp | None = None,
                 output_queue: Any = None):
        super().__init__(clock=clock, output_queue=output_queue)
        self.config = config or EndoscopeConfig()

    @property
    def name(self) -> str:
        return source_output_name(self.config.topic_name, self.config.arm_id)

    def _acquire_loop(self) -> None:
        logger.warning(
            'EndoscopeSource is a placeholder. Mode=%s — implement the '
            'actual SDK/HDMI/RTSP driver before recording endoscope data.',
            self.config.mode
        )
        # Keep the thread alive until stop() is called so the Session does not
        # consider this source prematurely finished.
        while not self._stop_event.is_set():
            time.sleep(0.1)

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary.update({
            'topic_name': self.config.topic_name,
            'arm_id': self.config.arm_id,
            'mode': self.config.mode,
            'ip': self.config.ip,
            'url_or_index': self.config.url_or_index,
            'requested_width': self.config.width,
            'requested_height': self.config.height,
            'requested_fps': self.config.fps,
        })
        return summary


class EndoscopeWriter:
    """Placeholder writer for endoscope frames.

    Real implementation will mirror USBCameraWriter and also persist SDK
    device timestamps when available.
    """

    def __init__(self, source_name: str, out_dir: Path):
        self.source_name = source_name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        logger.warning('EndoscopeWriter is a placeholder.')

    def write(self, sample: DataSample) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
