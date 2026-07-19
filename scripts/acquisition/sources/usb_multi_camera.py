"""Multi-USB camera source that opens all cameras in a single thread.

This is the recommended way to handle multiple identical USB cameras on
Windows. DirectShow cannot reliably open two identical cameras from separate
threads, but opening them sequentially in the same thread with different
resolutions works reliably.

Usage:
    - type: usb_multi_camera
      name: wrist_cameras
      configs:
        - camera_index: 1
          topic_name: camera/wrist_left/image_raw
          width: 640
          height: 480
          backend: dshow
        - camera_index: 2
          topic_name: camera/wrist_right/image_raw
          width: 320
          height: 240
          backend: dshow
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from ..base_source import DataSample, DataSource
from ..timestamp import UnifiedTimestamp
from .usb_camera import USBCameraConfig, USBCameraWriter

logger = logging.getLogger(__name__)


@dataclass
class USBMultiCameraConfig:
    """Configuration for a multi-USB camera source."""
    name: str = 'usb_multi_camera'
    configs: list[USBCameraConfig] | None = None

    def __post_init__(self):
        if self.configs is None:
            self.configs = [USBCameraConfig()]


class USBMultiCameraSource(DataSource):
    """Single-threaded multi-USB camera source.

    Opens all configured cameras sequentially in the acquisition thread and
    reads frames round-robin. Each camera produces samples under its own
    topic_name.
    """

    def __init__(self, config: USBMultiCameraConfig | None = None,
                 clock: UnifiedTimestamp | None = None,
                 output_queue: Any = None):
        super().__init__(clock=clock, output_queue=output_queue)
        self.config = config or USBMultiCameraConfig()
        self._caps: list[tuple[USBCameraConfig, cv2.VideoCapture | None]] = []

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def runs_in_main_thread(self) -> bool:
        """MSMF cameras must be opened and read in the main thread."""
        return any(cfg.backend == 'msmf' for cfg in self.config.configs)

    @staticmethod
    def _resolve_backend(name: str) -> int:
        mapping = {
            'dshow': cv2.CAP_DSHOW,
            'msmf': cv2.CAP_MSMF,
            'any': cv2.CAP_ANY,
        }
        if name not in mapping:
            raise ValueError(f"Unknown backend '{name}'. Use one of: {list(mapping.keys())}")
        return mapping[name]

    def prepare(self) -> None:
        """Open cameras in the main thread before any background sources start."""
        if self.runs_in_main_thread:
            self._caps = self._open_cameras()
            self._prepared = True

    def _open_cameras(self) -> list[tuple[USBCameraConfig, cv2.VideoCapture]]:
        """Open all cameras sequentially in the same thread."""
        opened = []
        for cfg in self.config.configs:
            backend = self._resolve_backend(cfg.backend)
            cap = cv2.VideoCapture(cfg.camera_index, backend)
            if not cap.isOpened():
                raise RuntimeError(
                    f"Cannot open USB camera index {cfg.camera_index} with backend '{cfg.backend}'"
                )

            if cfg.width is not None:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
            if cfg.height is not None:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
            if cfg.fps is not None:
                cap.set(cv2.CAP_PROP_FPS, cfg.fps)
            if cfg.fourcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cfg.fourcc[:4]))

            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            logger.info('%s camera %d opened at %dx%d',
                        self.name, cfg.camera_index, actual_w, actual_h)
            opened.append((cfg, cap))
        return opened

    def _acquire_loop(self) -> None:
        if not getattr(self, '_prepared', False):
            self._caps = self._open_cameras()

        while not self._stop_event.is_set():
            for cfg, cap in self._caps:
                if cap is None:
                    continue
                ret, frame = cap.read()
                if not ret:
                    logger.warning('%s frame read failed for camera %d',
                                   self.name, cfg.camera_index)
                    continue

                pc_arrival_ns = time.time_ns()
                arrival_ns = self.clock.now_ns()
                source_name = cfg.topic_name.replace('/', '_')
                sample = DataSample(
                    timestamp_ns=arrival_ns,
                    source_name=source_name,
                    data=frame,
                    metadata={
                        'topic': cfg.topic_name,
                        'camera_index': cfg.camera_index,
                        'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                        'pc_arrival_ns': pc_arrival_ns,
                    }
                )
                self._put_sample(sample)

    def _on_stop(self) -> None:
        for cfg, cap in self._caps:
            if cap is not None:
                cap.release()
                logger.info('%s camera %d released', self.name, cfg.camera_index)
        self._caps = []

    def get_writer_keys(self) -> list[str]:
        return [cfg.topic_name.replace('/', '_') for cfg in self.config.configs]

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary['cameras'] = [
            {
                'camera_index': cfg.camera_index,
                'topic_name': cfg.topic_name,
                'width': cfg.width,
                'height': cfg.height,
                'fps': cfg.fps,
                'backend': cfg.backend,
            }
            for cfg in self.config.configs
        ]
        return summary


class USBMultiCameraWriter:
    """Dispatches frames from a USBMultiCameraSource to per-camera writers."""

    def __init__(self, source_name: str, out_dir: Path):
        self.source_name = source_name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._writers: dict[str, USBCameraWriter] = {}

    def write(self, sample: DataSample) -> None:
        writer = self._writers.get(sample.source_name)
        if writer is None:
            writer = USBCameraWriter(sample.source_name, self.out_dir / sample.source_name)
            self._writers[sample.source_name] = writer
        writer.write(sample)

    def close(self) -> None:
        for writer in self._writers.values():
            writer.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
