"""Unified acquisition Session orchestrator.

A Session coordinates multiple DataSource instances, manages a unified
clock, writes samples to disk in a structured directory layout, and provides
start/stop control with graceful shutdown.

Directory layout for each recording session (actual, T12-P3-8):

    session_root/
    ├── session.json          # metadata: start time, source summaries, config
    ├── camera_arm_2/         # anchor (endoscope) frames + metadata.csv
    ├── camera_arm_1/         # wrist-left frames + metadata.csv
    ├── camera_arm_3/         # wrist-right frames + metadata.csv
    └── robot_pose/
        ├── meta.json             # T12-P1-2: constant connection metadata
        └── robot_pose_arm{1..4}/twincat_poses.csv   # per-arm samples

Adding a new modality:
  1. Subclass `DataSource` in `acquisition/sources/<modality>.py`.
  2. Provide a matching writer class if the source produces data to disk.
  3. Register the source and writer factories in `acquisition.registry`.
"""
from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from .base_source import DataSample, DataSource
from .registry import create_writer_for_source
from .timestamp import ClockConfig, TimestampSource, UnifiedTimestamp, ns_to_beijing_iso8601

logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    """Top-level configuration for an acquisition session."""
    session_name: str | None = None
    base_dir: str = './sessions'
    clock_source: str = 'host_realtime'  # 'host_realtime' | 'twincat_master' | 'manual_offset'
    clock_offset_ns: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)


class AcquisitionSession:
    """Orchestrates multiple data sources and writes samples to disk."""

    def __init__(self, config: SessionConfig):
        self.config = config
        self.session_name = config.session_name or self._default_session_name()
        self.session_dir = Path(config.base_dir).resolve() / self.session_name
        self.session_dir.mkdir(parents=True, exist_ok=True)

        clock_cfg = ClockConfig(
            source=self._parse_clock_source(config.clock_source),
            offset_ns=config.clock_offset_ns,
        )
        self.clock = UnifiedTimestamp(clock_cfg)

        self._shared_queue: Queue[DataSample] = Queue()
        self._sources: dict[str, DataSource] = {}
        self._writers: dict[str, Any] = {}
        self._writer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_at_ns: int | None = None
        self._stopped_at_ns: int | None = None
        self._samples_written: dict[str, int] = {}
        self._lock = threading.Lock()
        self._main_thread_sources: dict[str, DataSource] = {}

        # Graceful shutdown on Ctrl-C.
        signal.signal(signal.SIGINT, self._signal_handler)
        if sys.platform == 'win32':
            signal.signal(signal.SIGBREAK, self._signal_handler)

    @staticmethod
    def _default_session_name() -> str:
        return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

    @staticmethod
    def _parse_clock_source(source: str) -> TimestampSource:
        mapping = {
            'host_realtime': TimestampSource.HOST_REALTIME,
            'twincat_master': TimestampSource.TWINCAT_MASTER,
            'manual_offset': TimestampSource.MANUAL_OFFSET,
        }
        if source not in mapping:
            raise ValueError(f'Unknown clock source: {source}')
        return mapping[source]

    def register_source(self, source: DataSource) -> None:
        """Register a data source with the session."""
        if source.name in self._sources:
            raise ValueError(f'Duplicate source name: {source.name}')
        source.clock = self.clock
        source.output_queue = self._shared_queue
        source.attach_session(self.session_dir)
        self._sources[source.name] = source
        self._samples_written[source.name] = 0
        logger.info('Registered source: %s', source.name)

    @property
    def main_thread_sources(self) -> list[DataSource]:
        """Public access to sources that must run in the main thread."""
        return [s for s in self._sources.values() if s.runs_in_main_thread]

    def _signal_handler(self, signum, frame) -> None:
        logger.info('Received signal %s, stopping session gracefully...', signum)
        self.stop()

    def start(self) -> None:
        """Start all registered sources and the writer thread.

        Sources flagged with `runs_in_main_thread=True` are prepared here but
        their acquisition loop is not started; call `run_main_thread_sources()`
        from the main thread to run them.
        """
        if not self._sources:
            raise RuntimeError('No data sources registered')

        self._started_at_ns = self.clock.now_ns()
        self._stop_event.clear()
        self._main_thread_sources = {}

        # Separate sources that must run in the main thread (e.g. MSMF cameras).
        background_sources = {}
        for name, source in self._sources.items():
            if source.runs_in_main_thread:
                self._main_thread_sources[name] = source
            else:
                background_sources[name] = source

        # Create writers for known built-in sources.
        # A source may produce samples under multiple keys (e.g. multi-camera).
        for source in self._sources.values():
            writer = create_writer_for_source(source, self.session_dir / source.name)
            if writer is None:
                if source.get_writer_keys():
                    logger.warning(
                        'No disk writer registered for source type %s',
                        type(source).__name__,
                    )
                continue
            for key in source.get_writer_keys():
                self._writers[key] = writer

        self._writer_thread = threading.Thread(target=self._writer_loop, name='session-writer', daemon=True)
        self._writer_thread.start()

        # Prepare main-thread sources in the main thread BEFORE any background
        # source starts. This is required for backends like MSMF.
        for source in self._main_thread_sources.values():
            source.prepare()

        # Start background sources. Process-based camera workers block in
        # start() until their warm-up (auto white balance convergence) is done
        # and they signal ready. Two rules avoid the long serial start-up seen
        # in manual mode (T12-fix 2026-08-18):
        #   1. Fast sources (TwinCAT etc.) start FIRST so robot data is recorded
        #      for the whole session, not only after every camera warmed up.
        #   2. Camera sources are started in PARALLEL (one thread each) instead
        #      of serially, so N cameras cost max(warmup) instead of N*warmup.
        from .sources.usb_camera_process import USBCameraProcessSource

        def _startup_key(source):
            # TwinCAT / non-camera sources first (0), cameras last (1).
            return 1 if isinstance(source, USBCameraProcessSource) else 0

        fast_sources = [s for s in sorted(background_sources.values(), key=_startup_key)
                        if _startup_key(s) == 0]
        camera_sources = [s for s in sorted(background_sources.values(), key=_startup_key)
                          if _startup_key(s) == 1]

        for source in fast_sources:
            source.start()

        if camera_sources:
            threads = [
                threading.Thread(target=source.start, name=f'start-{source.name}', daemon=True)
                for source in camera_sources
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        main_thread_count = len(self._main_thread_sources)
        background_count = len(background_sources)
        logger.info('Session %s started: %d background source(s), %d main-thread source(s)',
                    self.session_name, background_count, main_thread_count)

    def run_main_thread_sources(self) -> None:
        """Run main-thread sources in the calling thread (intended to be main).

        This method blocks until `stop()` is called (e.g. via Ctrl-C). It is
        required for sources that use Windows Media Foundation or other
        main-thread-sensitive backends.
        """
        if not self._main_thread_sources:
            logger.info('No main-thread sources to run.')
            return

        logger.info('Running %d main-thread source(s)...', len(self._main_thread_sources))
        for source in self._main_thread_sources.values():
            source.run_in_main_thread()

        # If we exit the loop naturally (e.g. source finished), ensure the
        # whole session stops cleanly.
        self.stop()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop all sources and the writer, then flush metadata."""
        if self._stopped_at_ns is not None:
            return

        logger.info('Stopping session %s...', self.session_name)

        # Signal main-thread sources. Their acquisition loop checks the stop
        # event between frames. We do not join them here because they run in
        # the main thread that is calling this method (typically via Ctrl-C).
        for source in self._main_thread_sources.values():
            if source.is_running:
                logger.info('Signaling main-thread source %s to stop', source.name)

        # Stop background sources FIRST so all in-flight samples are enqueued
        # before the writer is told to exit. Setting _stop_event before this
        # let the writer loop exit while the queue was momentarily empty,
        # orphaning late samples (e.g. a TwinCAT source that started late after
        # camera warm-up) — reproduced as zero robot_pose CSV (fix 2026-08-18).
        for source in self._sources.values():
            if not source.runs_in_main_thread:
                source.stop(timeout=timeout / 2)

        # Now tell the writer to drain and exit.
        self._stop_event.set()

        # Wait for writer to drain. With high-resolution cameras PNG writing
        # can be slow, so give it most of the timeout budget.
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=timeout * 0.8)
            if self._writer_thread.is_alive():
                logger.warning('Writer thread did not stop in time')

        # Writers are closed by the writer thread itself to avoid races.

        self._stopped_at_ns = self.clock.now_ns()
        self._write_session_json()
        logger.info('Session %s stopped. Data: %s', self.session_name, self.session_dir)

    def _writer_loop(self) -> None:
        """Consume the shared queue and dispatch samples to writers."""
        try:
            while not self._stop_event.is_set() or not self._shared_queue.empty():
                try:
                    sample: DataSample = self._shared_queue.get(timeout=0.05)
                except Empty:
                    continue

                writer = self._writers.get(sample.source_name)
                if writer is not None and hasattr(writer, 'write'):
                    writer.write(sample)
                else:
                    logger.debug('No writer for source %s', sample.source_name)

                with self._lock:
                    self._samples_written[sample.source_name] = self._samples_written.get(sample.source_name, 0) + 1
        finally:
            # Close writers from within the writer thread to avoid races with
            # the main thread calling stop().
            for writer in dict.fromkeys(self._writers.values()):
                if hasattr(writer, 'close'):
                    writer.close()

    @staticmethod
    def _enrich_source_summary(src: DataSource) -> dict[str, Any]:
        """Add Beijing-time conversions to a source summary."""
        summary = src.get_summary()
        started_at_ns = summary.get('started_at_ns')
        stopped_at_ns = summary.get('stopped_at_ns')
        summary['started_at_beijing'] = ns_to_beijing_iso8601(started_at_ns) if started_at_ns else None
        summary['stopped_at_beijing'] = ns_to_beijing_iso8601(stopped_at_ns) if stopped_at_ns else None
        return summary

    def _write_session_json(self) -> None:
        """Persist session metadata and source summaries."""
        started_at_ns = self._started_at_ns
        stopped_at_ns = self._stopped_at_ns
        duration_s = ((stopped_at_ns or started_at_ns or 0) -
                      (started_at_ns or 0)) / 1e9

        # For process-based sources, the writer thread may not have written
        # anything; use the source's own sample_count as fallback.
        samples_written = {}
        for name, src in self._sources.items():
            count = self._samples_written.get(name, 0)
            if count == 0:
                count = src.sample_count
            samples_written[name] = count

        summary = {
            'session_name': self.session_name,
            'session_dir': str(self.session_dir),
            'started_at_ns': started_at_ns,
            'stopped_at_ns': stopped_at_ns,
            'started_at_beijing': ns_to_beijing_iso8601(started_at_ns) if started_at_ns else None,
            'stopped_at_beijing': ns_to_beijing_iso8601(stopped_at_ns) if stopped_at_ns else None,
            'duration_s': duration_s,
            'clock_source': self.config.clock_source,
            'clock_offset_ns': self.config.clock_offset_ns,
            'samples_written': samples_written,
            # T12-P1-1: non-zero worker exit codes surface mid-session camera
            # deaths; absent/0 means all workers stopped cleanly.
            'worker_exit_codes': {
                name: src.worker_exit_code
                for name, src in self._sources.items()
                if getattr(src, 'worker_exit_code', None) not in (None, 0)
            },
            'sources': [self._enrich_source_summary(src) for src in self._sources.values()],
        }
        with open(self.session_dir / 'session.json', 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)

    def wait_until_stopped(self) -> None:
        """Block until the session is stopped (e.g. by Ctrl-C)."""
        try:
            while not self._stop_event.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()

    def get_status(self) -> dict[str, Any]:
        return {
            'session_name': self.session_name,
            'running': self._writer_thread is not None and self._writer_thread.is_alive(),
            'samples_written': dict(self._samples_written),
            'sources': {name: src.get_summary() for name, src in self._sources.items()},
        }
