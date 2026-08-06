"""Built-in acquisition data sources."""

from .usb_camera_process import USBCameraProcessSource
from .legacy.endoscope import EndoscopeSource
from .legacy.usb_camera import USBCameraSource
from .legacy.usb_multi_camera import USBMultiCameraSource
from .reserved_arm import ReservedArmSource
from .twincat import TwinCATSource

__all__ = [
    'USBCameraSource',
    'USBMultiCameraSource',
    'USBCameraProcessSource',
    'EndoscopeSource',
    'ReservedArmSource',
    'TwinCATSource',
]
