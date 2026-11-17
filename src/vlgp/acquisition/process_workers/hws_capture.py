"""Minimal ctypes bindings for the HWS Capture SDK (创视之星采集卡).

Only what the acquisition worker needs:

  * device / capability enumeration;
  * callback-only RGB32 (= BGRA in memory, verified against the SDK's own
    OpenCV demo) frame capture with the SDK's per-frame ``timestamp100ns``
    and ``frameIndex`` — the whole point of this source is to timestamp
    frames at the capture card instead of at ``cv2.VideoCapture.read()``.

The SDK ships a plain C API (``hwscapture.h``), so no C++ build is needed.
The graph mirrors the SDK demo: ``VideoInput -> VideoRender(NULL)`` where the
render port works in callback-only mode (no D3D resources).
"""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

# Default install location of the vendor SDK on the collection PC; override
# with the HWS_SDK_DLL environment variable or the worker's --sdk-dll flag.
DEFAULT_DLL = r'D:\ProgramFiles\HWS_Capture_SDK_v6.0.0\binX64\hwscapture.dll'

HWS_OK = 0
HWS_PIXFMT_YUY2 = 1
HWS_PIXFMT_NV12 = 2
HWS_PIXFMT_RGB24 = 3
HWS_PIXFMT_RGB32 = 4          # BGRA byte order in memory
PIXEL_FORMAT_NAMES = {0: 'UNKNOWN', 1: 'YUY2', 2: 'NV12', 3: 'RGB24',
                      4: 'RGB32(BGRA)', 5: 'MJPG', 6: 'H264'}


class VideoInputConfig(ctypes.Structure):
    _fields_ = [('deviceIndex', ctypes.c_int),
                ('width', ctypes.c_int),
                ('height', ctypes.c_int),
                ('fps', ctypes.c_int),
                ('pixelFormat', ctypes.c_int),
                ('colorSpace', ctypes.c_int),
                ('colorRange', ctypes.c_int),
                ('bufferCount', ctypes.c_int)]


class VideoRenderConfig(ctypes.Structure):
    _fields_ = [('width', ctypes.c_int),
                ('height', ctypes.c_int),
                ('pixelFormat', ctypes.c_int)]


class DeviceInfo(ctypes.Structure):
    _fields_ = [('index', ctypes.c_int),
                ('name', ctypes.c_char * 256),
                ('id', ctypes.c_char * 512)]


class VideoFormat(ctypes.Structure):
    _fields_ = [('width', ctypes.c_int),
                ('height', ctypes.c_int),
                ('fpsNum', ctypes.c_int),
                ('fpsDen', ctypes.c_int),
                ('pixelFormat', ctypes.c_int)]


class VideoFrame(ctypes.Structure):
    _fields_ = [('data', ctypes.POINTER(ctypes.c_ubyte) * 4),
                ('stride', ctypes.c_int * 4),
                ('width', ctypes.c_int),
                ('height', ctypes.c_int),
                ('pixelFormat', ctypes.c_int),
                ('timestamp100ns', ctypes.c_longlong),
                ('frameIndex', ctypes.c_longlong)]


VIDEO_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.POINTER(VideoFrame),
                                    ctypes.c_void_p)


class HwsError(RuntimeError):
    pass


class HwsCapture:
    """One SDK capture graph (input + callback-only render) per device."""

    def __init__(self, dll_path: str | os.PathLike | None = None):
        path = str(dll_path or os.environ.get('HWS_SDK_DLL') or DEFAULT_DLL)
        if not Path(path).is_file():
            raise HwsError(f'HWS SDK DLL not found: {path}')
        # hwscapture.dll may depend on sibling DLLs next to it.
        self._dll_dir_handle = os.add_dll_directory(str(Path(path).parent))
        self.dll = ctypes.WinDLL(path)
        self._declare()
        self._input: int | None = None
        self._render: int | None = None
        self._callback_ref: VIDEO_CALLBACK | None = None  # keep alive vs GC
        self._started = False
        res = self.dll.HWS_Init()
        if res != HWS_OK:
            raise HwsError(f'HWS_Init failed: {res}')
        self._inited = True

    # ------------------------------------------------------------------ API
    def _declare(self) -> None:
        d = self.dll
        d.HWS_GetVersion.restype = ctypes.c_char_p
        d.HWS_Init.restype = ctypes.c_int
        d.HWS_Shutdown.restype = ctypes.c_int
        d.HWS_GetVideoDeviceCount.restype = ctypes.c_int
        d.HWS_GetVideoDeviceInfo.argtypes = [ctypes.c_int,
                                             ctypes.POINTER(DeviceInfo)]
        d.HWS_GetVideoDeviceInfo.restype = ctypes.c_int
        d.HWS_GetVideoFormatCount.argtypes = [ctypes.c_int]
        d.HWS_GetVideoFormatCount.restype = ctypes.c_int
        d.HWS_GetVideoFormatInfo.argtypes = [ctypes.c_int, ctypes.c_int,
                                             ctypes.POINTER(VideoFormat)]
        d.HWS_GetVideoFormatInfo.restype = ctypes.c_int
        d.HWS_CreateVideoInput.restype = ctypes.c_void_p
        d.HWS_CreateVideoRender.argtypes = [ctypes.c_void_p]
        d.HWS_CreateVideoRender.restype = ctypes.c_void_p
        d.HWS_SetVideoInputConfig.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(VideoInputConfig)]
        d.HWS_SetVideoInputConfig.restype = ctypes.c_int
        d.HWS_SetVideoRenderConfig.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(VideoRenderConfig)]
        d.HWS_SetVideoRenderConfig.restype = ctypes.c_int
        d.HWS_SetVideoFrameCallback.argtypes = [ctypes.c_void_p, VIDEO_CALLBACK,
                                                ctypes.c_void_p]
        d.HWS_SetVideoFrameCallback.restype = ctypes.c_int
        d.HWS_Connect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        d.HWS_Connect.restype = ctypes.c_int
        d.HWS_Start.argtypes = [ctypes.c_void_p]
        d.HWS_Start.restype = ctypes.c_int
        d.HWS_Stop.argtypes = [ctypes.c_void_p]
        d.HWS_Stop.restype = ctypes.c_int
        d.HWS_DestroyPort.argtypes = [ctypes.c_void_p]
        d.HWS_DestroyPort.restype = ctypes.c_int

    def version(self) -> str:
        return self.dll.HWS_GetVersion().decode('utf-8', errors='replace')

    def devices(self) -> list[dict]:
        out = []
        for i in range(self.dll.HWS_GetVideoDeviceCount()):
            info = DeviceInfo()
            if self.dll.HWS_GetVideoDeviceInfo(i, ctypes.byref(info)) == HWS_OK:
                out.append({'index': info.index,
                            'name': info.name.decode('utf-8', errors='replace'),
                            'id': info.id.decode('utf-8', errors='replace')})
        return out

    def formats(self, device_index: int) -> list[dict]:
        out = []
        for i in range(self.dll.HWS_GetVideoFormatCount(device_index)):
            fmt = VideoFormat()
            if self.dll.HWS_GetVideoFormatInfo(device_index, i,
                                               ctypes.byref(fmt)) == HWS_OK:
                out.append({'width': fmt.width, 'height': fmt.height,
                            'fps': fmt.fpsNum / max(fmt.fpsDen, 1),
                            'pixel_format': PIXEL_FORMAT_NAMES.get(
                                fmt.pixelFormat, str(fmt.pixelFormat))})
        return out

    def start(self, device_index: int, width: int, height: int, fps: int,
              on_frame: Callable[[np.ndarray, int, int], None],
              buffer_count: int = 4) -> None:
        """Start BGRA callback capture.

        ``on_frame(bgr, timestamp100ns, frame_index)`` receives an OWNED BGR
        uint8 array (H, W, 3) — the SDK buffer is copied inside the callback,
        so the array stays valid afterwards. Callbacks arrive on an SDK thread.
        """
        self._input = self.dll.HWS_CreateVideoInput()
        # hwnd = NULL -> callback-only render, no D3D resources.
        self._render = self.dll.HWS_CreateVideoRender(None)
        if not self._input or not self._render:
            raise HwsError('HWS_CreateVideoInput/Render failed')

        def _trampoline(frame_ptr, _user):
            try:
                f = frame_ptr.contents
                stride = f.stride[0] if f.stride[0] > 0 else f.width * 4
                nbytes = stride * f.height
                buf = np.frombuffer(
                    ctypes.string_at(ctypes.cast(f.data[0], ctypes.c_void_p),
                                     nbytes), dtype=np.uint8)
                bgra = buf.reshape(f.height, stride // 4, 4)[:, :f.width, :]
                # Copy BGR out: the SDK buffer is only valid inside this call.
                bgr = np.ascontiguousarray(bgra[:, :, :3])
                on_frame(bgr, f.timestamp100ns, f.frameIndex)
            except Exception:  # noqa: BLE001 — must not raise into the SDK
                logger.exception('HWS frame callback failed')

        self._callback_ref = VIDEO_CALLBACK(_trampoline)
        rcfg = VideoRenderConfig(width=width, height=height,
                                 pixelFormat=HWS_PIXFMT_RGB32)
        if self.dll.HWS_SetVideoRenderConfig(self._render,
                                             ctypes.byref(rcfg)) != HWS_OK:
            raise HwsError('HWS_SetVideoRenderConfig failed')
        if self.dll.HWS_SetVideoFrameCallback(
                self._render, self._callback_ref, None) != HWS_OK:
            raise HwsError('HWS_SetVideoFrameCallback failed')
        icfg = VideoInputConfig(deviceIndex=device_index, width=width,
                                height=height, fps=fps,
                                pixelFormat=HWS_PIXFMT_YUY2,
                                colorSpace=0, colorRange=0,
                                bufferCount=buffer_count)
        if self.dll.HWS_SetVideoInputConfig(self._input,
                                            ctypes.byref(icfg)) != HWS_OK:
            raise HwsError(f'HWS_SetVideoInputConfig failed for device '
                           f'{device_index} ({width}x{height}@{fps})')
        if self.dll.HWS_Connect(self._input, self._render) != HWS_OK:
            raise HwsError('HWS_Connect(input, render) failed')
        # SDK guide: start downstream first, then upstream.
        if self.dll.HWS_Start(self._render) != HWS_OK:
            raise HwsError('HWS_Start(render) failed')
        if self.dll.HWS_Start(self._input) != HWS_OK:
            raise HwsError('HWS_Start(input) failed — device busy or no signal?')
        self._started = True

    def stop(self) -> None:
        if self._input:
            self.dll.HWS_Stop(self._input)
        if self._render:
            self.dll.HWS_Stop(self._render)
        self._started = False

    def close(self) -> None:
        try:
            if self._started:
                self.stop()
            for port in (self._input, self._render):
                if port:
                    self.dll.HWS_DestroyPort(port)
            self._input = self._render = None
            self._callback_ref = None
            if getattr(self, '_inited', False):
                self.dll.HWS_Shutdown()
                self._inited = False
        finally:
            if getattr(self, '_dll_dir_handle', None) is not None:
                self._dll_dir_handle.close()
                self._dll_dir_handle = None


def grab_frame(device_index: int, width: int = 1280, height: int = 720,
               fps: int = 30, dll_path: str | None = None,
               timeout_s: float = 5.0):
    """Grab one frame through the SDK for preflight/preview, then release it.

    Returns an owned BGR array, or ``None`` when the device cannot be opened or
    no callback arrives before the timeout.
    """
    import threading

    result = {}
    received = threading.Event()
    capture = None

    def _on_first(frame, timestamp100ns, frame_index):
        if not received.is_set():
            result.update(frame=frame, timestamp100ns=timestamp100ns,
                          frame_index=frame_index)
            received.set()

    try:
        capture = HwsCapture(dll_path)
        capture.start(device_index, width, height, fps, _on_first)
        return result.get('frame') if received.wait(timeout_s) else None
    except (HwsError, OSError, AttributeError) as exc:
        logger.warning('HWS grab_frame(%d) failed: %s', device_index, exc)
        return None
    finally:
        if capture is not None:
            capture.close()
