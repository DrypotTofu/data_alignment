"""Camera binding + frame preview + acquisition preflight for the Web platform.

The web UI must answer "which physical camera is index N, and is it bound to the
right arm?" without a native folder/camera dialog. This module provides:

* :func:`scan_cameras` — try every camera index and report what opens
  (resolution/FPS), so the operator sees which indices exist.
* :func:`preview_jpeg` — open one index, grab one frame, return a JPEG. Opening
  the device per request (never holding it) is what keeps the preview from
  fighting the acquisition, which needs the same devices exclusively.
* :func:`config_cameras` / :func:`set_bindings` — read and comment-preserving
  write of the top-level ``camera_bindings`` mapping.
* :func:`preflight` — the three pre-recording checks: cameras openable, PLC/ADS
  link alive, and enough free space for the reserved recording time.

Storage model for the space check (measured on this rig's real sessions,
2026-09-12; 3 cameras PNG @1280x720 + 2x640x480, 30 Hz):

    test_20260901_144929  146.66 GB / 94263 rows = 1.63 MB/row -> 2.9 GB/min
    test_20260828_160624   24.75 GB / 13343 rows = 1.94 MB/row -> 3.5 GB/min
    test_20260828_155305   18.89 GB / 10579 rows = 1.87 MB/row -> 3.4 GB/min

so :data:`EST_GB_PER_MINUTE` = 3.2 GB/min is the default reservation rate
(20 minutes -> ~64 GB).
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path

import cv2

from vlgp.acquisition import session_layout as sl
from vlgp.acquisition.config import (
    load_config,
    read_base_dir,
    read_camera_bindings,
    set_camera_bindings,
)
from vlgp.capture import camera_probe

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_MAX_INDEX = 8                  # scan indices 0..8 (10 devices is plenty)
PREVIEW_MAX_W = 640
JPEG_QUALITY = 82
EST_GB_PER_MINUTE = 3.2             # see module docstring (measured 2.9-3.5)
DEFAULT_RESERVE_MINUTES = 20.0
CAMERA_SOURCE_TYPES = {'process_camera', 'usb_camera', 'endoscope'}
GB = 1024 ** 3


# --------------------------------------------------------------------- scan
def scan_cameras(max_index: int = SCAN_MAX_INDEX,
                 backend: str = 'dshow') -> dict:
    """Open every index in turn and report what works (device released after)."""
    t0 = time.perf_counter()
    entries = []
    for index in range(0, int(max_index) + 1):
        cap = camera_probe.open_camera(index, backend)
        if cap is None:
            entries.append({'index': index, 'opened': False})
            continue
        try:
            w, h, fps = camera_probe.probe_default_state(cap)
            ret, frame = cap.read()
            usable = bool(ret and frame is not None and frame.size > 0)
            entries.append({'index': index, 'opened': True,
                            'width': w, 'height': h, 'fps': round(float(fps), 2),
                            'frame_read': usable})
        finally:
            cap.release()
    return {'backend': backend, 'max_index': int(max_index),
            'elapsed_s': round(time.perf_counter() - t0, 2),
            'entries': entries,
            'found': [e['index'] for e in entries if e.get('opened')]}


def preview_jpeg(index: int, width: int = PREVIEW_MAX_W,
                 height: int = 480, backend: str = 'dshow',
                 fourcc: str | None = None) -> tuple[bytes, dict]:
    """One JPEG frame from a camera index (device is opened and released).

    Raises RuntimeError when the device cannot be opened or read — the API maps
    that to 422 with the message shown in the UI.
    """
    t0 = time.perf_counter()
    frame = camera_probe.grab_frame(int(index), int(width), int(height), backend,
                                    fourcc=fourcc)
    if frame is None:
        raise RuntimeError(
            f'index {index} 打不开或取不到帧（可能被其他程序占用，或不是相机）')
    h, w = frame.shape[:2]
    if w > PREVIEW_MAX_W:
        scale = PREVIEW_MAX_W / w
        frame = cv2.resize(frame, (PREVIEW_MAX_W, max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError('JPEG encode failed')
    return buf.tobytes(), {'width': w, 'height': h,
                           'elapsed_s': round(time.perf_counter() - t0, 2)}


# ----------------------------------------------------------------- bindings
def config_cameras(path: str | Path) -> dict:
    """Current bindings + the camera sources of one acquisition config."""
    p = Path(path)
    out: dict = {'path': str(p), 'exists': p.is_file(),
                 'bindings': read_camera_bindings(p) or {},
                 'base_dir': read_base_dir(p), 'cameras': [], 'errors': []}
    if not p.is_file():
        return out
    try:
        cfg = load_config(p)
    except ValueError as e:
        out['errors'].append(str(e))
        return out
    for source in cfg.sources:
        if source.get('type') not in CAMERA_SOURCE_TYPES:
            continue
        out['cameras'].append({
            'name': source.get('name') or source.get('type'),
            'arm_id': source.get('arm_id'),
            'camera_index': source.get('camera_index'),
            'width': source.get('width'), 'height': source.get('height'),
            'fps': source.get('fps'), 'target_fps': source.get('target_fps'),
            'fourcc': source.get('fourcc'), 'backend': source.get('backend'),
            'image_format': source.get('image_format'),
            'enabled': source.get('enabled') is not False,
        })
    return out


def set_bindings(path: str | Path, bindings: dict[str, int]) -> dict:
    """Write arm->index bindings into one config after validating them.

    Validation happens on a scratch copy first: ``load_config`` rejects a
    duplicate ``camera_index`` or an arm with no matching source, so a bad
    binding can never reach the real file (which the rig's launchers use).
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError(f'配置文件不存在: {p}')
    clean: dict[str, int] = {}
    for key, value in (bindings or {}).items():
        key = str(key)
        if not key.startswith('arm_'):
            raise ValueError(f'非法绑定键: {key!r}（应形如 arm_1）')
        try:
            idx = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'{key} 的 index 必须是整数，收到 {value!r}')
        if idx < 0:
            raise ValueError(f'{key} 的 index 不能为负')
        clean[key] = idx
    if not clean:
        raise ValueError('没有要写入的绑定')

    scratch = REPO_ROOT / 'var' / 'tmp_bind_check.yaml'
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(p.read_text(encoding='utf-8'), encoding='utf-8')
    try:
        set_camera_bindings(scratch, clean)
        load_config(scratch)          # raises ValueError on conflicts
    except ValueError as e:
        raise ValueError(str(e))
    finally:
        scratch.unlink(missing_ok=True)

    result = set_camera_bindings(p, clean)
    return {**result, 'bindings': read_camera_bindings(p) or {},
            'cameras': config_cameras(p)['cameras'],
            'warnings': binding_warnings(p)}


def binding_warnings(path: str | Path) -> list[str]:
    """Non-fatal binding problems the validator does not cover.

    ``validate_session_config`` only checks ENABLED camera sources, so binding
    two arms to the same index is accepted while one of them is disabled — which
    then breaks the day that source is enabled. Surface it as a warning.
    """
    info = config_cameras(path)
    by_index: dict[int, list[str]] = {}
    for cam in info.get('cameras', []):
        idx = cam.get('camera_index')
        if idx is None:
            continue
        by_index.setdefault(int(idx), []).append(
            f"{cam['name']}{'' if cam.get('enabled') else '(已禁用)'}")
    out = []
    for idx, names in sorted(by_index.items()):
        if len(names) > 1:
            out.append(f'index {idx} 被多个相机源使用: {", ".join(names)}'
                       + ('（其中含已禁用源，启用后会冲突）'
                          if any('已禁用' in n for n in names) else ''))
    for cam in info.get('cameras', []):
        if cam.get('enabled') and cam.get('camera_index') is None:
            out.append(f"{cam['name']} 没有绑定 index")
    return out


# ---------------------------------------------------------------- preflight
def _robot_check(source: dict, timeout_s: float = 5.0) -> dict:
    """Connect over ADS and read one symbol (read-only, no side effects)."""
    ams_net_id = source.get('ams_net_id', '169.254.226.99.1.1')
    ams_port = int(source.get('ams_port', 851))
    ip = source.get('ip_address', '192.168.22.22')
    symbols = source.get('symbols') or {}
    symbol = (symbols.get('joint_angles') or symbols.get('armframe_positions')
              or 'MAIN.MonitorTask')
    try:
        import pyads
    except ImportError as e:                     # pragma: no cover
        return {'ok': False, 'detail': f'pyads 不可用: {e}'}
    t0 = time.perf_counter()
    plc = pyads.Connection(ams_net_id, ams_port, ip)
    try:
        plc.open()
        plc.set_timeout(int(timeout_s * 1000))
        value = plc.read_by_name(symbol, pyads.PLCTYPE_ARR_REAL(32))
        dt = (time.perf_counter() - t0) * 1000
        return {'ok': True, 'symbol': symbol,
                'detail': f'ADS 连接成功 {ams_net_id}:{ams_port} via {ip}，'
                          f'读取 {symbol} 用时 {dt:.0f} ms（前 3 值 '
                          f'{[round(float(v), 3) for v in list(value)[:3]]}）'}
    except Exception as e:                       # noqa: BLE001 — report, never raise
        return {'ok': False, 'symbol': symbol,
                'detail': f'ADS 连接/读取失败 {ams_net_id}:{ams_port} via {ip}: {e}'}
    finally:
        try:
            plc.close()
        except Exception:                        # noqa: BLE001
            pass


def _disk_check(base_dir: str | None, reserve_minutes: float) -> dict:
    if not base_dir:
        return {'ok': False, 'detail': '配置里没有 base_dir，无法预估磁盘占用'}
    p = Path(base_dir).expanduser()
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    probe_dir = p
    while not probe_dir.exists() and probe_dir.parent != probe_dir:
        probe_dir = probe_dir.parent
    try:
        usage = shutil.disk_usage(str(probe_dir))
    except OSError as e:
        return {'ok': False, 'detail': f'无法读取磁盘信息 {probe_dir}: {e}'}
    need_gb = EST_GB_PER_MINUTE * float(reserve_minutes)
    free_gb = usage.free / GB
    detail = (f'{probe_dir} 剩余 {free_gb:.0f} GB；按 {EST_GB_PER_MINUTE} GB/分钟'
              f' 预留 {reserve_minutes:.0f} 分钟 ≈ 需 {need_gb:.0f} GB')
    return {'ok': free_gb >= need_gb, 'detail': detail,
            'free_gb': round(free_gb, 1), 'need_gb': round(need_gb, 1),
            'reserve_minutes': float(reserve_minutes),
            'gb_per_minute': EST_GB_PER_MINUTE, 'target_dir': str(p)}


def suggest_session_name(prefix: str = 'test') -> str:
    return datetime.now().strftime(f'{prefix}_%Y%m%d_%H%M%S')


def session_dir_for(base_dir: str | None, name: str) -> Path | None:
    if not base_dir:
        return None
    p = Path(base_dir).expanduser()
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p / name


def preflight(config_path: str | Path, reserve_minutes: float = DEFAULT_RESERVE_MINUTES,
              session_name: str | None = None,
              check_cameras: bool = True,
              check_robot: bool = True) -> dict:
    """Pre-recording checks (cameras / PLC link / free space / name clash)."""
    p = Path(config_path)
    checks: list[dict] = []
    try:
        cfg = load_config(p)
        load_error = None
    except ValueError as e:
        cfg, load_error = None, str(e)
    if load_error:
        return {'ok': False, 'config': str(p),
                'checks': [{'id': 'config', 'label': '配置可加载', 'status': 'fail',
                            'detail': load_error}],
                'estimate': {'gb_per_minute': EST_GB_PER_MINUTE,
                             'reserve_minutes': float(reserve_minutes)}}

    checks.append({'id': 'config', 'label': '配置可加载', 'status': 'ok',
                   'detail': f'{len(cfg.sources)} 个源，时钟={cfg.clock_source}'})

    # ---- A. cameras
    cameras = [s for s in cfg.sources
               if s.get('type') in CAMERA_SOURCE_TYPES and s.get('enabled') is not False]
    if not cameras:
        checks.append({'id': 'cameras', 'label': '相机', 'status': 'warn',
                       'detail': '配置中没有启用的相机源'})
    elif not check_cameras:
        checks.append({'id': 'cameras', 'label': '相机', 'status': 'warn',
                       'detail': '已跳过相机自检'})
    else:
        bad, ok_list = [], []
        for cam in cameras:
            index = cam.get('camera_index')
            if index is None:
                bad.append(f"{cam.get('name')}: 未绑定 index")
                continue
            frame = camera_probe.grab_frame(int(index),
                                            int(cam.get('width') or 640),
                                            int(cam.get('height') or 480),
                                            cam.get('backend') or 'dshow',
                                            fourcc=cam.get('fourcc'))
            if frame is None:
                bad.append(f"{cam.get('name')} (index {index}): 打不开/取不到帧")
            else:
                h, w = frame.shape[:2]
                ok_list.append(f"{cam.get('name')}=index{index} {w}x{h}")
        checks.append({'id': 'cameras', 'label': '相机自检',
                       'status': 'ok' if not bad else 'fail',
                       'detail': '；'.join(ok_list + bad) or '无相机'})

    # ---- B. robot link
    twincats = [s for s in cfg.sources
                if s.get('type') == 'twincat' and s.get('enabled') is not False]
    if not twincats:
        checks.append({'id': 'robot', 'label': '机器人链路', 'status': 'warn',
                       'detail': '配置中没有启用的 twincat 源'})
    elif not check_robot:
        checks.append({'id': 'robot', 'label': '机器人链路', 'status': 'warn',
                       'detail': '已跳过机器人自检'})
    else:
        robot = _robot_check(twincats[0])
        checks.append({'id': 'robot', 'label': '机器人链路 (ADS)',
                       'status': 'ok' if robot['ok'] else 'fail',
                       'detail': robot['detail']})

    # ---- C. disk reservation for the expected recording time
    disk = _disk_check(cfg.base_dir, reserve_minutes)
    checks.append({'id': 'disk', 'label': '磁盘余量',
                   'status': 'ok' if disk['ok'] else 'fail',
                   'detail': disk['detail']})

    # ---- D. session folder must not already exist (a clashing name would
    #         append new recordings into an existing session folder)
    name = session_name or suggest_session_name()
    sess_dir = session_dir_for(cfg.base_dir, name)
    clash = bool(sess_dir and sess_dir.exists())
    checks.append({'id': 'name', 'label': '会话目录',
                   'status': 'fail' if clash else 'ok',
                   'detail': (f'{sess_dir} 已存在 —— 换一个会话名（同名会写入同一目录）'
                              if clash else f'{sess_dir} 可用')})

    failed = [c for c in checks if c['status'] == 'fail']
    return {'ok': not failed, 'config': str(p), 'checks': checks,
            'failed': [c['id'] for c in failed],
            'session_name': name, 'session_dir': str(sess_dir) if sess_dir else None,
            'base_dir': cfg.base_dir,
            'estimate': {'gb_per_minute': EST_GB_PER_MINUTE,
                         'reserve_minutes': float(reserve_minutes),
                         'reserve_gb': round(EST_GB_PER_MINUTE * float(reserve_minutes), 1)},
            'alignment_hint': (f'{sl.ALIGNMENT_REL} 需在录制后生成'
                               if sess_dir else None)}
