"""Unified timestamp utilities for the acquisition framework.

The framework uses nanosecond-resolution timestamps. The default source is
the host CLOCK_REALTIME (`time.time_ns()`). When a TwinCAT ADS connection is
available, the Session can be configured to use TwinCAT as the master clock
and convert host timestamps to the TwinCAT time base.
"""
from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from enum import Enum, auto


class TimestampSource(Enum):
    """Supported timestamp sources."""
    HOST_REALTIME = auto()      # time.time_ns()
    TWINCAT_MASTER = auto()     # TwinCAT clock as master; host ns converted via offset
    MANUAL_OFFSET = auto()      # user-provided fixed offset


@dataclass
class ClockConfig:
    """Configuration for the unified timestamp provider."""
    source: TimestampSource = TimestampSource.HOST_REALTIME
    # Offset to add to host time_ns to align with master clock ( TwinCAT ).
    offset_ns: int = 0


class UnifiedTimestamp:
    """Thread-safe-ish unified timestamp provider.

    This object is intentionally simple and does not hold locks; it is assumed
    that clock configuration is set once at session start and then read-only.
    """

    def __init__(self, config: ClockConfig | None = None):
        self.config = config or ClockConfig()

    def now_ns(self) -> int:
        """Return current timestamp in nanoseconds."""
        return get_timestamp_ns(self.config)


def get_timestamp_ns(config: ClockConfig | None = None) -> int:
    """Return a unified timestamp in nanoseconds.

    Currently defaults to host CLOCK_REALTIME. When TwinCAT is integrated,
    the same call site can return a TwinCAT-aligned timestamp.
    """
    cfg = config or ClockConfig()
    if cfg.source == TimestampSource.TWINCAT_MASTER:
        # In the future this can query TwinCAT via ADS; for now convert host.
        return time.time_ns() + cfg.offset_ns
    if cfg.source == TimestampSource.MANUAL_OFFSET:
        return time.time_ns() + cfg.offset_ns
    return time.time_ns()


def filetime_to_ns(ft: int) -> int:
    """Convert Windows FILETIME (100-ns ticks) to Unix-epoch nanoseconds."""
    return (ft - 116444736000000000) * 100


def ns_to_datetime(ns: int, tz: datetime.timezone | None = None) -> datetime.datetime:
    """Convert Unix-epoch nanoseconds to a timezone-aware datetime."""
    dt = datetime.datetime.fromtimestamp(ns / 1e9, tz=tz or datetime.timezone.utc)
    return dt


def ns_to_iso8601(ns: int, tz: datetime.timezone | None = None) -> str:
    """Convert Unix-epoch nanoseconds to an ISO-8601 string."""
    return ns_to_datetime(ns, tz).isoformat(timespec='milliseconds')


BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8), name='Asia/Shanghai')


def ns_to_beijing_iso8601(ns: int) -> str:
    """Convert Unix-epoch nanoseconds to Beijing (UTC+8) ISO-8601 string."""
    return ns_to_iso8601(ns, tz=BEIJING_TZ)
