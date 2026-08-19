"""Abstract base class for acquisition data sources.

To add a new data modality, subclass `DataSource` and implement:
  - `name` property
  - `start()`
  - `stop()`
  - `_acquire_loop()` (or equivalent producer)
  - optionally `get_summary()`
"""
from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from .timestamp import ClockConfig, UnifiedTimestamp

logger = logging.getLogger(__name__)


@dataclass
class DataSample:
    """A single sample produced by a data source.

    Attributes:
        timestamp_ns: timestamp in the unified clock domain (nanoseconds)
        source_name: name of the source that produced the sample
        data: raw data; exact type is source-dependent (numpy array, dict, etc.)
        metadata: optional per-sample metadata (dict must be JSON-serializable)
    """
    timestamp_ns: int
    source_name: str
    data: Any
    metadata: dict[str, Any] = field(default_factory=dict)


class DataSource(abc.ABC):
    """Abstract base class for all acquisition data sources."""

    def __init__(self, clock: UnifiedTimestamp | None = None,
                 output_queue: Queue[DataSample] | None = None):
        self.clock = clock or UnifiedTimestamp()
        self.output_queue: Queue[DataSample] = output_queue or Queue()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False
        self._lock = threading.Lock()
        self._sample_count = 0
        self._started_at_ns: int | None = None
        self._stopped_at_ns: int | None = None

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return a human-readable unique name for this source."""
        raise NotImplementedError

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def runs_in_main_thread(self) -> bool:
        """Return True if this source must run in the main thread.

        Sources that use Windows Media Foundation (MSMF) or have other
        main-thread dependencies should override this to True.
        """
        return False

    def prepare(self) -> None:
        """Prepare resources in the main thread before background sources start.

        Override if the source needs to open devices in the main thread but
        read them later (e.g. MSMF cameras).
        """
        pass

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._sample_count

    def start(self) -> None:
        """Start the acquisition thread."""
        if self.is_running:
            logger.warning('%s already running', self.name)
            return
        self._started_at_ns = self.clock.now_ns() if self.clock else None
        self._stopped_at_ns = None
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f'{self.name}-acq', daemon=True)
        self._thread.start()
        self._started = True
        logger.info('Started source: %s', self.name)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the acquisition thread to stop and wait for it."""
        if not self.is_running:
            return
        logger.info('Stopping source: %s', self.name)
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning('Source %s did not stop within %.1fs', self.name, timeout)
        else:
            logger.info('Stopped source: %s', self.name)
        self._stopped_at_ns = self.clock.now_ns() if self.clock else None
        self._thread = None
        self._started = False

    def _run(self) -> None:
        """Wrapper that calls the source-specific loop and handles errors."""
        try:
            self._acquire_loop()
        except Exception:
            logger.exception('Acquisition loop failed in source %s', self.name)
        finally:
            self._on_stop()

    @abc.abstractmethod
    def _acquire_loop(self) -> None:
        """Main acquisition loop. Must check `_stop_event` periodically."""
        raise NotImplementedError

    def _on_stop(self) -> None:
        """Hook called after the acquisition loop exits. Override if needed."""
        pass

    def _put_sample(self, sample: DataSample) -> None:
        """Enqueue a sample and update counters."""
        with self._lock:
            self._sample_count += 1
        self.output_queue.put(sample)

    def attach_session(self, session_dir: Path | None) -> None:
        """Attach the owning session's directory (T12-P2-6, replaces private
        ``_session_dir`` pokes from the framework)."""
        self._session_dir = Path(session_dir) if session_dir else None

    def run_in_main_thread(self) -> None:
        """Run this source's acquisition loop in the calling (main) thread.

        Encapsulates the start/stop bookkeeping that the session previously
        performed by poking private attributes (T12-P2-6). Returns when the
        source stops.
        """
        self._started_at_ns = self.clock.now_ns() if self.clock else None
        self._stopped_at_ns = None
        self._stop_event.clear()
        try:
            self._acquire_loop()
        except Exception:
            logger.exception('Main-thread acquisition loop failed in source %s', self.name)
        finally:
            self._on_stop()
            self._stopped_at_ns = self.clock.now_ns() if self.clock else None

    def get_writer_keys(self) -> list[str]:
        """Return the keys under which this source's samples should be written.

        Most sources produce samples with `source_name == self.name`. Multi-
        camera sources may produce samples under child names and should override
        this method.
        """
        return [self.name]

    def get_summary(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of this source's configuration."""
        return {
            'name': self.name,
            'running': self.is_running,
            'sample_count': self.sample_count,
            'started_at_ns': self._started_at_ns,
            'stopped_at_ns': self._stopped_at_ns,
        }

    def drain(self, max_samples: int = 1000) -> list[DataSample]:
        """Non-blocking drain of the output queue. Useful for tests."""
        samples = []
        for _ in range(max_samples):
            try:
                samples.append(self.output_queue.get_nowait())
            except Empty:
                break
        return samples
