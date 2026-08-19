"""USB camera acquisition source using OpenCV.

Features:
  - Non-blocking capture thread
  - Frame-arrival timestamp in unified clock domain
  - Per-frame PNG output + metadata.csv
  - Optional ROS-like topic name for session bookkeeping
"""
from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ...base_source import DataSample, DataSource
from ...naming import source_output_name
from ...timestamp import UnifiedTimestamp, ns_to_beijing_iso8601

logger = logging.getLogger(__name__)


@dataclass
class USBCameraConfig:
    """Configuration for a USB camera source."""
    camera_index: int = 0
    topic_name: str = 'camera/wrist_left/image_raw'
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    fourcc: str = 'MJPG'       # 'MJPG' or 'YUY2'
    backend: str = 'dshow'     # 'dshow', 'msmf', or 'any' (cv2.CAP_*)
    save_images: bool = True
    image_format: str = 'png'  # 'png' or 'jpg'
    arm_id: int | None = None  # if set, output name becomes camera_arm_<id>


class USBCameraSource(DataSource):
    """Data source for USB cameras (OpenCV VideoCapture)."""

    def __init__(self, config: USBCameraConfig | None = None,
                 clock: UnifiedTimestamp | None = None,
                 output_queue: Any = None):
        super().__init__(clock=clock, output_queue=output_queue)
        self.config = config or USBCameraConfig()
        self._cap: cv2.VideoCapture | None = None

    @property
    def name(self) -> str:
        return source_output_name(self.config.topic_name, self.config.arm_id)

    @property
    def runs_in_main_thread(self) -> bool:
        """MSMF cameras must be opened and read in the main thread on Windows."""
        return self.config.backend == 'msmf'

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

    def _open_camera(self) -> cv2.VideoCapture:
        """Open and configure the VideoCapture device."""
        backend = self._resolve_backend(self.config.backend)
        cap = cv2.VideoCapture(self.config.camera_index, backend)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open USB camera index {self.config.camera_index} with backend '{self.config.backend}'")

        if self.config.width is not None:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        if self.config.height is not None:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        if self.config.fps is not None:
            cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        if self.config.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC,
                    cv2.VideoWriter_fourcc(*self.config.fourcc[:4]))

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        logger.info('%s opened at %dx%d @ %.2f fps', self.name, actual_w, actual_h, actual_fps)
        return cap

    def prepare(self) -> None:
        """Open the camera in the main thread before background sources start.

        This is essential for MSMF-backed cameras and avoids deadlocks when
        DirectShow cameras are already running in background threads.
        """
        if self.runs_in_main_thread and self._cap is None:
            self._cap = self._open_camera()
            self._prepared = True

    def _acquire_loop(self) -> None:
        if getattr(self, '_prepared', False):
            cap = self._cap
        else:
            cap = self._open_camera()
            self._cap = cap

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        while not self._stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                logger.warning('%s frame read failed', self.name)
                break

            pc_arrival_ns = time.time_ns()
            arrival_ns = self.clock.now_ns()
            sample = DataSample(
                timestamp_ns=arrival_ns,
                source_name=self.name,
                data=frame,
                metadata={
                    'topic': self.config.topic_name,
                    'width': actual_w,
                    'height': actual_h,
                    'camera_index': self.config.camera_index,
                    'pc_arrival_ns': pc_arrival_ns,
                }
            )
            self._put_sample(sample)

    def _on_stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info('%s released', self.name)

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary.update({
            'camera_index': self.config.camera_index,
            'topic_name': self.config.topic_name,
            'arm_id': self.config.arm_id,
            'requested_width': self.config.width,
            'requested_height': self.config.height,
            'requested_fps': self.config.fps,
            'fourcc': self.config.fourcc,
            'backend': self.config.backend,
        })
        if self._cap is not None and self._cap.isOpened():
            summary.update({
                'actual_width': int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                'actual_height': int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                'actual_fps': self._cap.get(cv2.CAP_PROP_FPS),
            })
        return summary


class USBCameraWriter:
    """Consumes USB camera samples and writes frames + metadata.csv."""

    def __init__(self, source_name: str, out_dir: Path, image_format: str = 'png'):
        self.source_name = source_name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.image_format = image_format.lower().lstrip('.')
        self.metadata_path = self.out_dir / 'metadata.csv'
        self._csv_file = open(self.metadata_path, 'w', newline='')
        self._writer = csv.writer(self._csv_file)
        self._writer.writerow(['index', 'timestamp_ns', 'pc_arrival_ns', 'timestamp_beijing', 'filename', 'width', 'height'])
        self._frame_idx = 0

    def write(self, sample: DataSample) -> None:
        filename = f'{self.source_name}_{self._frame_idx:08d}_{sample.timestamp_ns}.{self.image_format}'
        filepath = self.out_dir / filename
        if self.image_format in ('png',):
            cv2.imwrite(str(filepath), sample.data, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        elif self.image_format in ('jpg', 'jpeg'):
            cv2.imwrite(str(filepath), sample.data, [cv2.IMWRITE_JPEG_QUALITY, 95])
        else:
            cv2.imwrite(str(filepath), sample.data)
        h, w = sample.data.shape[:2]
        beijing_time = ns_to_beijing_iso8601(sample.timestamp_ns)
        pc_arrival_ns = sample.metadata.get('pc_arrival_ns', sample.timestamp_ns)
        self._writer.writerow([self._frame_idx, sample.timestamp_ns, pc_arrival_ns, beijing_time, filename, w, h])
        self._frame_idx += 1

    def close(self) -> None:
        self._csv_file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
