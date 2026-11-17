"""Process-based HWS capture-card source with device frame timestamps."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .usb_camera_process import USBCameraProcessConfig, USBCameraProcessSource


@dataclass
class HWSCameraProcessConfig(USBCameraProcessConfig):
    """USB-process settings plus the HWS SDK-specific controls."""

    backend: str = 'hws_sdk'
    fourcc: str = ''
    sdk_dll: str | None = None
    buffer_count: int = 4


class HWSCameraProcessSource(USBCameraProcessSource):
    """Reuse the robust process lifecycle; swap in the HWS callback worker."""

    WORKER_MODULE = 'vlgp.acquisition.process_workers.hws_camera_worker'

    def __init__(self, config: HWSCameraProcessConfig | None = None, **kwargs):
        super().__init__(config=config or HWSCameraProcessConfig(), **kwargs)

    def _extra_worker_args(self) -> list[str]:
        args = ['--buffer-count', str(self.config.buffer_count)]
        if self.config.sdk_dll:
            args += ['--sdk-dll', self.config.sdk_dll]
        return args

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary.update({
            'backend': 'hws_sdk',
            'timestamp_source': 'hws_device_100ns_anchored_to_pc',
            'buffer_count': self.config.buffer_count,
        })
        return summary
