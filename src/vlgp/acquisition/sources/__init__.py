"""Active built-in acquisition data sources.

Legacy sources remain importable from ``acquisition.sources.legacy`` for old
archived configs/tests, but are not part of the active public surface.
"""

from .usb_camera_process import USBCameraProcessSource
from .reserved_arm import ReservedArmSource
from .twincat import TwinCATSource

__all__ = [
    'USBCameraProcessSource',
    'ReservedArmSource',
    'TwinCATSource',
]
