"""Central registry for acquisition source and writer factories.

New modalities should be registered here rather than adding ad-hoc branches in
both ``run_acquisition.py`` and ``session.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .base_source import DataSource
from .sources.legacy.endoscope import EndoscopeConfig, EndoscopeSource, EndoscopeWriter
from .sources.legacy.usb_camera import USBCameraConfig, USBCameraSource, USBCameraWriter
from .sources.legacy.usb_multi_camera import USBMultiCameraConfig, USBMultiCameraSource, USBMultiCameraWriter
from .sources.reserved_arm import ReservedArmConfig, ReservedArmSource
from .sources.twincat import TwinCATConfig, TwinCATSource, TwinCATWriter
from .sources.usb_camera_process import USBCameraProcessConfig, USBCameraProcessSource

SourceBuilder = Callable[[dict[str, Any]], DataSource]
WriterFactory = Callable[[DataSource, Path], Any | None]


def _build_usb_camera(source_cfg: dict[str, Any]) -> DataSource:
    name = source_cfg.get('name', 'usb_camera')
    cfg = USBCameraConfig(
        camera_index=source_cfg.get('camera_index', 0),
        topic_name=source_cfg.get('topic_name', name),
        width=source_cfg.get('width'),
        height=source_cfg.get('height'),
        fps=source_cfg.get('fps'),
        fourcc=source_cfg.get('fourcc', 'MJPG'),
        backend=source_cfg.get('backend', 'dshow'),
        save_images=source_cfg.get('save_images', True),
        image_format=source_cfg.get('image_format', 'png'),
        arm_id=source_cfg.get('arm_id'),
    )
    return USBCameraSource(config=cfg)


def _build_usb_multi_camera(source_cfg: dict[str, Any]) -> DataSource:
    name = source_cfg.get('name', 'usb_multi_camera')
    cam_configs = [USBCameraConfig(**c) for c in source_cfg.get('configs', [])]
    cfg = USBMultiCameraConfig(name=name, configs=cam_configs)
    return USBMultiCameraSource(config=cfg)


def _build_endoscope(source_cfg: dict[str, Any]) -> DataSource:
    name = source_cfg.get('name', 'endoscope')
    cfg = EndoscopeConfig(
        topic_name=source_cfg.get('topic_name', name),
        mode=source_cfg.get('mode', 'sdk'),
        ip=source_cfg.get('ip'),
        username=source_cfg.get('username'),
        password=source_cfg.get('password'),
        url_or_index=source_cfg.get('url_or_index'),
        width=source_cfg.get('width'),
        height=source_cfg.get('height'),
        fps=source_cfg.get('fps'),
        arm_id=source_cfg.get('arm_id'),
    )
    return EndoscopeSource(config=cfg)


def _build_twincat(source_cfg: dict[str, Any]) -> DataSource:
    name = source_cfg.get('name', 'twincat')
    cfg = TwinCATConfig(
        topic_name=source_cfg.get('topic_name', name),
        ams_net_id=source_cfg.get('ams_net_id', '169.254.226.99.1.1'),
        ams_port=source_cfg.get('ams_port', 851),
        ip_address=source_cfg.get('ip_address', '192.168.22.22'),
        local_ams_net_id=source_cfg.get('local_ams_net_id'),
        cycle_period_ms=source_cfg.get('cycle_period_ms', 1.0),
        symbols=source_cfg.get('symbols'),
        symbol_shapes=source_cfg.get('symbol_shapes'),
        selected_arm=source_cfg.get('selected_arm', 1),
        timestamp_mode=source_cfg.get('timestamp_mode', 'pc_arrival'),
        twincat_timestamp_symbol=source_cfg.get(
            'twincat_timestamp_symbol',
            'TwinCAT_SystemInfoVarList._TaskInfo[1].CycleCount',
        ),
        twincat_timestamp_cycle_time_symbol=source_cfg.get(
            'twincat_timestamp_cycle_time_symbol',
            'TwinCAT_SystemInfoVarList._TaskInfo[1].CycleTime',
        ),
        twincat_timestamp_cycles_per_ms=source_cfg.get(
            'twincat_timestamp_cycles_per_ms'
        ),
        twincat_master_sync_alpha=source_cfg.get(
            'twincat_master_sync_alpha', 0.05
        ),
        use_rtt_correction=source_cfg.get('use_rtt_correction', False),
        output_dual_timestamps=source_cfg.get('output_dual_timestamps', False),
        num_connections=source_cfg.get('num_connections', 1),
        output_all_arms=source_cfg.get('output_all_arms', False),
    )
    return TwinCATSource(config=cfg)


def _build_process_camera(source_cfg: dict[str, Any]) -> DataSource:
    name = source_cfg.get('name', 'process_camera')
    cfg = USBCameraProcessConfig(
        camera_index=source_cfg.get('camera_index', 0),
        topic_name=source_cfg.get('topic_name', name),
        width=source_cfg.get('width'),
        height=source_cfg.get('height'),
        fps=source_cfg.get('fps'),
        fourcc=source_cfg.get('fourcc', 'MJPG'),
        backend=source_cfg.get('backend', 'dshow'),
        image_format=source_cfg.get('image_format', 'png'),
        stop_file=source_cfg.get('stop_file', '.stop'),
        ready_file=source_cfg.get('ready_file', '.ready'),
        ready_timeout=source_cfg.get('ready_timeout', 10.0),
        warmup_s=source_cfg.get('warmup_s', 1.5),
        target_fps=source_cfg.get('target_fps'),
        png_compression=source_cfg.get('png_compression', 1),
        writer_threads=source_cfg.get('writer_threads', 1),
        arm_id=source_cfg.get('arm_id'),
    )
    return USBCameraProcessSource(config=cfg)


def _build_reserved_arm(source_cfg: dict[str, Any]) -> DataSource:
    name = source_cfg.get('name', 'reserved_arm')
    cfg = ReservedArmConfig(
        arm_id=source_cfg.get('arm_id'),
        topic_name=source_cfg.get('topic_name', name),
    )
    return ReservedArmSource(config=cfg)


SOURCE_BUILDERS: dict[str, SourceBuilder] = {
    'usb_camera': _build_usb_camera,
    'usb_multi_camera': _build_usb_multi_camera,
    'endoscope': _build_endoscope,
    'twincat': _build_twincat,
    'process_camera': _build_process_camera,
    'reserved_arm': _build_reserved_arm,
}


def build_source_from_config(source_cfg: dict[str, Any]) -> DataSource:
    """Build a data source from one YAML source mapping."""
    kind = source_cfg.get('type')
    builder = SOURCE_BUILDERS.get(kind)
    if builder is None:
        known = ', '.join(sorted(SOURCE_BUILDERS))
        raise ValueError(f'Unknown source type: {kind}. Known types: {known}')
    return builder(source_cfg)


def _usb_camera_writer(source: DataSource, source_dir: Path) -> Any:
    return USBCameraWriter(
        source.name,
        source_dir,
        image_format=source.config.image_format,
    )


def _usb_multi_camera_writer(source: DataSource, source_dir: Path) -> Any:
    return USBMultiCameraWriter(source.name, source_dir)


def _endoscope_writer(source: DataSource, source_dir: Path) -> Any:
    return EndoscopeWriter(source.name, source_dir)


def _twincat_writer(source: DataSource, source_dir: Path) -> Any:
    return TwinCATWriter(source.name, source_dir)


WRITER_FACTORIES: list[tuple[type[DataSource], WriterFactory]] = [
    (USBMultiCameraSource, _usb_multi_camera_writer),
    (USBCameraSource, _usb_camera_writer),
    (EndoscopeSource, _endoscope_writer),
    (TwinCATSource, _twincat_writer),
]

DIRECT_DISK_SOURCE_TYPES = (USBCameraProcessSource, ReservedArmSource)


def create_writer_for_source(source: DataSource, source_dir: Path) -> Any | None:
    """Create the session writer for a source, or None for direct-to-disk sources."""
    if isinstance(source, DIRECT_DISK_SOURCE_TYPES):
        return None
    for source_type, factory in WRITER_FACTORIES:
        if isinstance(source, source_type):
            return factory(source, source_dir)
    return None
