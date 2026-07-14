"""CLI entry point for the VLGP acquisition framework.

Usage:
    python run_acquisition.py --config config/example_acquisition.yaml
    python run_acquisition.py --duration 10

The config file defines which sources to enable and their parameters.
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from acquisition import AcquisitionSession
from acquisition.session import SessionConfig
from acquisition.sources import EndoscopeSource, TwinCATSource, USBCameraSource, USBMultiCameraSource, USBCameraProcessSource, ReservedArmSource
from acquisition.sources.endoscope import EndoscopeConfig
from acquisition.sources.reserved_arm import ReservedArmConfig
from acquisition.sources.twincat import TwinCATConfig
from acquisition.sources.usb_camera import USBCameraConfig
from acquisition.sources.usb_multi_camera import USBMultiCameraConfig
from acquisition.sources.usb_camera_process import USBCameraProcessConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def build_source_from_config(source_cfg: dict[str, Any]):
    """Factory that builds a DataSource from a YAML/JSON config dict."""
    kind = source_cfg.get('type')
    name = source_cfg.get('name', kind)

    if kind == 'usb_camera':
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

    if kind == 'usb_multi_camera':
        cam_configs = [USBCameraConfig(**c) for c in source_cfg.get('configs', [])]
        cfg = USBMultiCameraConfig(name=name, configs=cam_configs)
        return USBMultiCameraSource(config=cfg)

    if kind == 'endoscope':
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

    if kind == 'twincat':
        cfg = TwinCATConfig(
            topic_name=source_cfg.get('topic_name', name),
            ams_net_id=source_cfg.get('ams_net_id', '192.168.1.100.1.1'),
            ams_port=source_cfg.get('ams_port', 851),
            ip_address=source_cfg.get('ip_address', '192.168.1.100'),
            local_ams_net_id=source_cfg.get('local_ams_net_id'),
            cycle_period_ms=source_cfg.get('cycle_period_ms', 1.0),
            symbols=source_cfg.get('symbols'),
        )
        return TwinCATSource(config=cfg)

    if kind == 'process_camera':
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
            arm_id=source_cfg.get('arm_id'),
        )
        return USBCameraProcessSource(config=cfg)

    if kind == 'reserved_arm':
        cfg = ReservedArmConfig(
            arm_id=source_cfg.get('arm_id'),
            topic_name=source_cfg.get('topic_name', name),
        )
        return ReservedArmSource(config=cfg)

    raise ValueError(f'Unknown source type: {kind}')


def load_config(path: str) -> SessionConfig:
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return SessionConfig(**data)


def main():
    parser = argparse.ArgumentParser(description='VLGP multi-modal acquisition framework')
    parser.add_argument('--config', type=str,
                        default=str(Path(__file__).parent / 'config' / 'example_acquisition.yaml'),
                        help='Path to YAML config file')
    parser.add_argument('--duration', type=float, default=None,
                        help='If set, stop recording after N seconds')
    parser.add_argument('--name', type=str, default=None,
                        help='Override session name')
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.name:
        cfg.session_name = args.name

    session = AcquisitionSession(cfg)
    for source_cfg in cfg.sources:
        source = build_source_from_config(source_cfg)
        session.register_source(source)

    session.start()

    # Check whether any source must run in the main thread.
    main_thread_sources = [s for s in session._sources.values() if s.runs_in_main_thread]

    if main_thread_sources:
        # For timed recordings with main-thread sources, start a background
        # timer that calls stop() after the requested duration. The timer must
        # be started AFTER the cameras have been prepared in the main thread.
        if args.duration:
            def _stop_after(seconds: float) -> None:
                time.sleep(seconds)
                session.stop()

            timer = threading.Thread(
                target=_stop_after,
                args=(args.duration,),
                name='session-duration-timer',
                daemon=True
            )
            timer.start()
            logger.info('Recording for %.1f seconds...', args.duration)
        else:
            logger.info('Recording indefinitely. Press Ctrl-C to stop.')

        # Run main-thread sources in the main thread. This blocks.
        try:
            session.run_main_thread_sources()
        except KeyboardInterrupt:
            session.stop()

        if args.duration:
            timer.join(timeout=1.0)
    else:
        # Original behavior: background sources only.
        if args.duration:
            logger.info('Recording for %.1f seconds...', args.duration)
            try:
                time.sleep(args.duration)
            except KeyboardInterrupt:
                pass
            session.stop()
        else:
            logger.info('Recording indefinitely. Press Ctrl-C to stop.')
            session.wait_until_stopped()


if __name__ == '__main__':
    main()
