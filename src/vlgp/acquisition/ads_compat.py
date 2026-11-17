"""Compatibility helpers for the Beckhoff Windows ADS DLL loader.

On this PC, ``TcAdsDll.dll!AdsPortOpenEx`` returns Win32 error 126 unless
``AdsDll.dll`` has already been loaded.  Preloading the sibling core DLL fixes
the native probe and pyads without changing TwinCAT services, routes, or the
system-wide DLL search path (verified with the 64-bit project environment).
"""
from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
_ads_core_handle = None


def ensure_ads_core_loaded() -> bool:
    """Preload pyads' sibling ``AdsDll.dll`` on Windows, once per process.

    Returns False only when the compatibility load was needed but failed.  The
    normal pyads open path remains responsible for reporting connection errors.
    """
    global _ads_core_handle
    if sys.platform != 'win32' or _ads_core_handle is not None:
        return True
    try:
        from pyads import pyads_ex
        core_path = Path(pyads_ex.dll_path) / 'AdsDll.dll'
        if not core_path.is_file():
            logger.warning('ADS compatibility DLL not found: %s', core_path)
            return False
        _ads_core_handle = ctypes.WinDLL(str(core_path))
        logger.info('Preloaded ADS core DLL: %s', core_path)
        return True
    except (ImportError, AttributeError, OSError) as exc:
        logger.warning('Could not preload ADS core DLL: %s', exc)
        return False
