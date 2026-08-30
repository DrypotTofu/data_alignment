"""Manual start/stop acquisition runner.

Unlike run_acquisition.py --duration N, this script waits for you to press
Enter to begin recording, then waits for another Enter (or Ctrl-C) to stop.
Useful when the recording length is not known in advance.

Usage:
    python -m vlgp.capture.manual_acquisition --config config/default.yaml
    python -m vlgp.capture.manual_acquisition --config config/default.yaml --name my_session
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


from vlgp.acquisition import AcquisitionSession
from vlgp.acquisition.config import load_config


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def default_session_name() -> str:
    """Generate a unique session name: test_YYYYMMDD_HHMMSS (local time)."""
    return datetime.now().strftime('test_%Y%m%d_%H%M%S')


def wait_for_enter(prompt: str, stop_event: threading.Event | None = None) -> None:
    """Block until the user presses Enter.

    If stop_event is provided, signal it when Enter is pressed so the main
    recording loop can exit gracefully.
    """
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        pass
    if stop_event is not None:
        stop_event.set()


def print_status(session: AcquisitionSession, stop_event: threading.Event,
                 interval_s: float = 2.0) -> None:
    """Periodically print source frame/sample counts until stop_event is set."""
    while not stop_event.is_set():
        status = session.get_status()
        samples = status.get('samples_written', {})
        parts = [f'{name}={count}' for name, count in sorted(samples.items())]
        print(f'  [recording] {" ".join(parts)}', flush=True)
        # Sleep in small chunks so we react quickly to stop_event.
        for _ in range(int(interval_s * 2)):
            if stop_event.is_set():
                break
            time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description='Manual start/stop acquisition')
    parser.add_argument('--config', type=str,
                        default=str(Path(__file__).parents[3] / 'scripts' / 'config' / 'default.yaml'),
                        help='Path to YAML config file')
    parser.add_argument('--name', type=str, default=None,
                        help='Override session name')
    parser.add_argument('--status-interval', type=float, default=2.0,
                        help='Seconds between status updates while recording')
    args = parser.parse_args()

    cfg = load_config(args.config)
    cfg.session_name = args.name if args.name else default_session_name()

    session = AcquisitionSession.from_config(cfg)

    if not session.sources:
        logger.error('No sources enabled in config.')
        return 1

    print('\n' + '=' * 70)
    print('Manual acquisition mode')
    print(f'Config: {Path(args.config).resolve()}')
    print(f'Session: {session.session_name}')
    print(f'Sources: {", ".join(session.sources.keys())}')
    print('=' * 70)

    print('\nPress Enter to START recording...')
    wait_for_enter('')

    session.start()
    stop_event = threading.Event()

    # Listener thread for stop key.
    input_thread = threading.Thread(
        target=wait_for_enter,
        args=('Press Enter to STOP recording...\n', stop_event),
        name='stop-listener',
        daemon=True,
    )
    input_thread.start()

    # Status printer.
    status_thread = threading.Thread(
        target=print_status,
        args=(session, stop_event, args.status_interval),
        name='status-printer',
        daemon=True,
    )
    status_thread.start()

    print('\nRecording started. Press Enter to stop.')
    try:
        while not stop_event.is_set():
            time.sleep(0.1)
    except KeyboardInterrupt:
        print('\nInterrupted by user.')
    finally:
        stop_event.set()
        session.stop(timeout=30.0)
        input_thread.join(timeout=1.0)
        status_thread.join(timeout=1.0)

    print('\n' + '=' * 70)
    print(f'Session stopped. Data saved to: {session.session_dir}')
    print('=' * 70)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
