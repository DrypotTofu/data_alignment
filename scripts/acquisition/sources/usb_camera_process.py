"""Process-based USB camera source.

Each camera runs in a separate OS process, which avoids Windows DirectShow
threading conflicts when using multiple identical USB cameras at the same
resolution. The subprocess writes frames and metadata.csv directly to disk.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base_source import DataSource, UnifiedTimestamp
from ..naming import source_output_name

logger = logging.getLogger(__name__)


@dataclass
class USBCameraProcessConfig:
    """Configuration for a process-based USB camera source."""
    camera_index: int = 0
    topic_name: str = 'camera/image_raw'
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    fourcc: str = 'MJPG'
    backend: str = 'dshow'
    image_format: str = 'png'
    arm_id: int | None = None  # if set, output name becomes camera_arm_<id>
    duration: float | None = None  # if None, runs until stop signal
    stop_file: str = '.stop'
    ready_file: str = '.ready'
    ready_timeout: float = 10.0


class USBCameraProcessSource(DataSource):
    """Data source that captures a USB camera in a separate process."""

    def __init__(self, config: USBCameraProcessConfig | None = None,
                 clock: UnifiedTimestamp | None = None,
                 output_queue: Any = None):
        # We do not use the base threading machinery; override key pieces.
        super().__init__(clock=clock, output_queue=output_queue)
        self.config = config or USBCameraProcessConfig()
        self._process: subprocess.Popen | None = None
        self._out_dir: Path | None = None
        self._frame_count = 0

    @property
    def name(self) -> str:
        return source_output_name(self.config.topic_name, self.config.arm_id)

    @property
    def runs_in_main_thread(self) -> bool:
        return False

    def _build_out_dir(self, session_dir: Path | None = None) -> Path:
        if session_dir is not None:
            return session_dir / self.name
        return Path('./sessions') / self.name

    def start(self) -> None:
        """Spawn the camera worker subprocess."""
        if self._process is not None and self._process.poll() is None:
            logger.warning('%s already running', self.name)
            return

        # Try to use the session directory if this source is part of a Session.
        session_dir = getattr(self, '_session_dir', None)
        self._out_dir = self._build_out_dir(session_dir)
        self._out_dir.mkdir(parents=True, exist_ok=True)

        self._started_at_ns = self.clock.now_ns() if self.clock else None
        self._stopped_at_ns = None
        self._frame_count = 0

        cmd = [
            sys.executable,
            '-m',
            'acquisition.process_workers.usb_camera_worker',
            '--index', str(self.config.camera_index),
            '--topic', self.config.topic_name,
            '--out-dir', str(self._out_dir),
            '--backend', self.config.backend,
            '--image-format', self.config.image_format,
            '--stop-file', self.config.stop_file,
            '--ready-file', self.config.ready_file,
            '--source-name', self.name,
            '--topic', self.config.topic_name,
        ]
        if self.config.width is not None:
            cmd += ['--width', str(self.config.width)]
        if self.config.height is not None:
            cmd += ['--height', str(self.config.height)]
        if self.config.fps is not None:
            cmd += ['--fps', str(self.config.fps)]
        if self.config.fourcc:
            cmd += ['--fourcc', self.config.fourcc]
        if self.config.duration is not None:
            cmd += ['--duration', str(self.config.duration)]

        logger.info('Starting worker process for %s: %s', self.name, ' '.join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
        )
        self._wait_for_ready()
        self._started = True

    def stop(self, timeout: float = 10.0) -> None:
        """Signal the worker process to stop and wait for it."""
        if self._process is None:
            return

        logger.info('Stopping process source %s', self.name)
        if self._out_dir is not None:
            stop_path = self._out_dir / self.config.stop_file
            try:
                stop_path.write_text('')
            except Exception as e:
                logger.warning('Could not write stop file: %s', e)

        try:
            stdout, _ = self._process.communicate(timeout=timeout)
            if stdout:
                for line in stdout.strip().splitlines():
                    logger.info('[worker %s] %s', self.name, line)
        except subprocess.TimeoutExpired:
            logger.warning('Worker %s did not stop in time, terminating', self.name)
            self._process.terminate()
            try:
                stdout, _ = self._process.communicate(timeout=2)
                if stdout:
                    for line in stdout.strip().splitlines():
                        logger.info('[worker %s] %s', self.name, line)
            except subprocess.TimeoutExpired:
                self._process.kill()

        self._stopped_at_ns = self.clock.now_ns() if self.clock else None
        self._process = None
        self._started = False
        self._frame_count = self._read_frame_count()
        with self._lock:
            self._sample_count = self._frame_count
        logger.info('Stopped process source %s (%d frames)', self.name, self._frame_count)

    def _wait_for_ready(self) -> None:
        """Block until the worker signals it has captured the first frame."""
        if self._out_dir is None:
            return
        ready_path = self._out_dir / self.config.ready_file
        deadline = time.time() + self.config.ready_timeout
        while time.time() < deadline:
            if ready_path.exists():
                logger.info('Worker %s is ready', self.name)
                return
            if self._process is not None and self._process.poll() is not None:
                logger.error('Worker %s exited before signalling ready', self.name)
                return
            time.sleep(0.05)
        logger.warning('Worker %s did not signal ready within %.1fs', self.name, self.config.ready_timeout)

    def _read_frame_count(self) -> int:
        """Read the number of frames from the worker's metadata.csv."""
        if self._out_dir is None:
            return 0
        meta_path = self._out_dir / 'metadata.csv'
        if not meta_path.exists():
            return 0
        try:
            with open(meta_path, 'r', encoding='utf-8-sig', newline='') as f:
                import csv
                reader = csv.DictReader(f)
                return sum(1 for _ in reader)
        except Exception as e:
            logger.warning('Could not read metadata for %s: %s', self.name, e)
            return 0

    def _acquire_loop(self) -> None:
        """Not used; capture happens in the subprocess."""
        pass

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary.update({
            'camera_index': self.config.camera_index,
            'topic_name': self.config.topic_name,
            'arm_id': self.config.arm_id,
            'width': self.config.width,
            'height': self.config.height,
            'fps': self.config.fps,
            'backend': self.config.backend,
            'image_format': self.config.image_format,
            'process_based': True,
        })
        return summary

    def get_writer_keys(self) -> list[str]:
        # Process-based source writes directly to disk; no Session writer needed.
        return []
