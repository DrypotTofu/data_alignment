"""Deprecated acquisition sources kept for backward compatibility.

These sources are superseded by `usb_camera_process.USBCameraProcessSource`
(process-per-camera) and are retained only so old configs still load.
"""

from .endoscope import EndoscopeSource
from .usb_camera import USBCameraSource
from .usb_multi_camera import USBMultiCameraSource

__all__ = ['EndoscopeSource', 'USBCameraSource', 'USBMultiCameraSource']
