"""VLGP multi-modal data acquisition framework.

This package provides a unified Session orchestrator and pluggable data
sources for collecting synchronous multi-modal surgical-robot data.
"""

from .session import AcquisitionSession
from .base_source import DataSource, DataSample
from .timestamp import TimestampSource, get_timestamp_ns

__all__ = [
    'AcquisitionSession',
    'DataSource',
    'DataSample',
    'TimestampSource',
    'get_timestamp_ns',
]
