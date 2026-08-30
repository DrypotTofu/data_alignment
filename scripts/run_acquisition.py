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

from vlgp.acquisition import AcquisitionSession
from vlgp.acquisition.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


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

    session = AcquisitionSession.from_config(cfg)
    session.start()

    # Check whether any source must run in the main thread.
    main_thread_sources = session.main_thread_sources

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
