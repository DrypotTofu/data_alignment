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
    # Offset added to the worker's frame arrival time so that timestamps align
    # with the session master clock (e.g. twincat_master). The base source
    # passes this value from the shared UnifiedTimestamp at worker startup.
    clock_offset_ns: int = 0
    duration: float | None = None  # if None, runs until stop signal
    stop_file: str = '.stop'
    ready_file: str = '.ready'
    ready_timeout: float = 10.0
    # Seconds of frames the worker discards right after opening the camera, so
    # auto white balance / exposure converge before recording starts.
    warmup_s: float = 1.5
    # If set, the driver is left in its native (fast) mode and the worker keeps
    # frames on a virtual 1/target_fps grid for an exact output rate.
    target_fps: float | None = None
    # PNG compression level 0-9 (lossless at every level). Production config
    # (T3, 2026-08-17): PNG L1 + writer_threads=6 sustains 720p@30 fps on this
    # machine; level 0 was only needed for the old dual-writer 720p config.
    png_compression: int = 1
    # Parallel image-writer threads per camera. 720p PNG L1 needs 6 threads to
    # hold 30 fps end-to-end on this machine (T3/T12 verified); 480p is fine
    # with 1-2.
    writer_threads: int = 1
    # T11: manual exposure / white balance (None = driver default, see worker).
    exposure: float | None = None
    wb_temperature: float | None = None
    auto_wb: int | None = None
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None


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
            return (session_dir / self.name).resolve()
        return Path('./sessions').resolve() / self.name

    def start(self) -> None:
        """Spawn the camera worker subprocess."""
        if self._process is not None and self._process.poll() is None:
            logger.warning('%s already running', self.name)
            return

        # Try to use the session directory if this source is part of a Session.
        # (attach_session sets _session_dir; T12-P2-6 removed the getattr poke.)
        session_dir = self._session_dir
        self._out_dir = self._build_out_dir(session_dir)
        self._out_dir.mkdir(parents=True, exist_ok=True)

        self._started_at_ns = self.clock.now_ns() if self.clock else None
        self._stopped_at_ns = None
        self._frame_count = 0

        cmd = [
            sys.executable,
            '-m',
            'vlgp.acquisition.process_workers.usb_camera_worker',
            '--index', str(self.config.camera_index),
            '--topic', self.config.topic_name,
            '--out-dir', str(self._out_dir),
            '--backend', self.config.backend,
            '--image-format', self.config.image_format,
            '--stop-file', self.config.stop_file,
            '--ready-file', self.config.ready_file,
            '--source-name', self.name,
            '--clock-offset-ns', str(
                self.clock.config.offset_ns if self.clock is not None else self.config.clock_offset_ns
            ),
        ]
        if self.config.width is not None:
            cmd += ['--width', str(self.config.width)]
        if self.config.height is not None:
            cmd += ['--height', str(self.config.height)]
        if self.config.fps is not None:
            cmd += ['--fps', str(self.config.fps)]
        if self.config.target_fps is not None:
            cmd += ['--target-fps', str(self.config.target_fps)]
        cmd += ['--png-compression', str(self.config.png_compression)]
        if self.config.writer_threads != 1:
            cmd += ['--writer-threads', str(self.config.writer_threads)]
        if self.config.fourcc:
            cmd += ['--fourcc', self.config.fourcc]
        if self.config.duration is not None:
            cmd += ['--duration', str(self.config.duration)]
        if self.config.warmup_s is not None:
            cmd += ['--warmup-s', str(self.config.warmup_s)]
        if self.config.exposure is not None:
            cmd += ['--exposure', str(self.config.exposure)]
        if self.config.wb_temperature is not None:
            cmd += ['--wb-temperature', str(self.config.wb_temperature)]
        if self.config.auto_wb is not None:
            cmd += ['--auto-wb', str(self.config.auto_wb)]
        if self.config.brightness is not None:
            cmd += ['--brightness', str(self.config.brightness)]
        if self.config.contrast is not None:
            cmd += ['--contrast', str(self.config.contrast)]
        if self.config.saturation is not None:
            cmd += ['--saturation', str(self.config.saturation)]

        logger.info('Starting worker process for %s: %s', self.name, ' '.join(cmd))
        # T13: package is pip-installed; worker runs via its vlgp module path,
        # no manual PYTHONPATH needed. cwd is irrelevant for -m module launch.
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

        exit_code: int | None = None
        try:
            stdout, _ = self._process.communicate(timeout=timeout)
            exit_code = self._process.returncode
            if stdout:
                for line in stdout.strip().splitlines():
                    logger.info('[worker %s] %s', self.name, line)
        except subprocess.TimeoutExpired:
            logger.warning('Worker %s did not stop in time, terminating', self.name)
            self._process.terminate()
            try:
                stdout, _ = self._process.communicate(timeout=2)
                exit_code = self._process.returncode
                if stdout:
                    for line in stdout.strip().splitlines():
                        logger.info('[worker %s] %s', self.name, line)
            except subprocess.TimeoutExpired:
                self._process.kill()
                exit_code = -9

        # T12-P1-1: record a non-zero worker exit so the session.json exposes
        # a mid-session camera death instead of hiding it.
        if exit_code not in (None, 0):
            logger.error('Worker %s exited with code %d (camera may have died '
                         'mid-session)', self.name, exit_code)
        self._worker_exit_code = exit_code
        self._stopped_at_ns = self.clock.now_ns() if self.clock else None
        self._process = None
        self._started = False
        self._frame_count = self._read_frame_count()
        with self._lock:
            self._sample_count = self._frame_count
        logger.info('Stopped process source %s (%d frames)', self.name, self._frame_count)

    @property
    def worker_exit_code(self) -> int | None:
        """Non-zero if the worker died abnormally (T12-P1-1 observability)."""
        return getattr(self, '_worker_exit_code', None)

    def _wait_for_ready(self) -> None:
        """Block until the worker signals it has captured the first frame.

        T12-P1-1: if the worker process exits before signalling ready, raise so
        the session start aborts loudly instead of recording a silent gap.
        """
        if self._out_dir is None:
            return
        ready_path = self._out_dir / self.config.ready_file
        deadline = time.time() + self.config.ready_timeout
        while time.time() < deadline:
            if ready_path.exists():
                logger.info('Worker %s is ready', self.name)
                return
            if self._process is not None and self._process.poll() is not None:
                code = self._process.returncode
                stderr = self._process.stderr.read() if self._process.stderr else ''
                raise RuntimeError(
                    f'Worker for {self.name} exited before signalling ready '
                    f'(exit code {code}). Camera index {self.config.camera_index} '
                    f'failed to open or died during warm-up.\n{stderr[-800:]}')
            time.sleep(0.05)
        raise RuntimeError(
            f'Worker {self.name} did not signal ready within '
            f'{self.config.ready_timeout:.1f}s (camera index '
            f'{self.config.camera_index} not opening)')

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
