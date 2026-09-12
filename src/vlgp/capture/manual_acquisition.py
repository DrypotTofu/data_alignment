"""Manual start/stop acquisition runner.

Unlike run_acquisition.py --duration N, this script waits for you to press
Enter to begin recording, then waits for another Enter (or Ctrl-C) to stop.
Useful when the recording length is not known in advance.

Non-interactive control (web platform / scripts): pass ``--stop-file PATH``.
Recording then starts immediately (no Enter needed) and stops as soon as PATH
exists; add ``--stop-on-stdin-eof`` so a controller that holds an open stdin
pipe also stops the recording when it dies (never enable it when stdin may be
redirected from NUL — that reads as EOF and stops at once). The log gets
parseable markers:

    === ACQ SESSION <name> DIR <dir>
    === ACQ RECORDING STARTED
    === ACQ RECORDING STOPPED reason=<...> elapsed=<s>s
    === ACQ DONE dir=<dir> samples=<name=count ...>

Usage:
    python -m vlgp.capture.manual_acquisition --config config/default.yaml
    python -m vlgp.capture.manual_acquisition --config config/default.yaml --name my_session
    python -m vlgp.capture.manual_acquisition --config config/default.yaml \
        --name my_session --stop-file var/web_acq/stop.flag
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
from vlgp.acquisition.stop_signal import StopSignal, stdin_is_tty


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
    parser.add_argument('--stop-file', type=str, default=None,
                        help='Non-interactive: start immediately and stop when '
                             'this file exists')
    parser.add_argument('--stop-on-stdin-eof', action='store_true',
                        help='Also stop when stdin closes (fail-safe for a '
                             'controller that holds an open stdin pipe; do NOT '
                             'enable it when stdin may be redirected from NUL)')
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
    print(f'Source dir: {session.session_dir}')
    print(f'Sources: {", ".join(session.sources.keys())}')
    print('=' * 70)
    print(f'=== ACQ SESSION {session.session_name} DIR {session.session_dir}',
          flush=True)

    # Non-interactive mode: --stop-file starts immediately (a piped stdin can
    # never deliver the Enter the interactive flow waits for).
    if args.stop_file:
        print(f'Auto-start (--stop-file {args.stop_file})', flush=True)
        stop_event = threading.Event()
        signal_ = StopSignal(stop_file=args.stop_file,
                             watch_stdin=args.stop_on_stdin_eof,
                             event=stop_event).arm()
        input_thread = None
    else:
        if not stdin_is_tty():
            # Enter can never arrive on a pipe; without --stop-file the session
            # would start and stop again immediately — say so instead of
            # leaving the operator wondering why nothing was recorded.
            logger.warning('stdin is not a terminal: the interactive Enter flow '
                           'cannot work here — pass --stop-file to control the '
                           'recording from a script/UI')
        print('\nPress Enter to START recording...')
        wait_for_enter('')
        stop_event = threading.Event()
        signal_ = None
        input_thread = threading.Thread(
            target=wait_for_enter,
            args=('Press Enter to STOP recording...\n', stop_event),
            name='stop-listener',
            daemon=True,
        )
        input_thread.start()

    session.start()
    started_at = time.time()
    print(f'=== ACQ RECORDING STARTED name={session.session_name} '
          f"dir={session.session_dir} t={datetime.now().isoformat(timespec='seconds')}",
          flush=True)

    # Status printer.
    status_thread = threading.Thread(
        target=print_status,
        args=(session, stop_event, args.status_interval),
        name='status-printer',
        daemon=True,
    )
    status_thread.start()

    if args.stop_file:
        print(f'\nRecording. Stop by creating {args.stop_file} '
              f'(or close stdin / Ctrl-C).', flush=True)
    else:
        print('\nRecording started. Press Enter to stop.')
    try:
        while not stop_event.is_set():
            time.sleep(0.1)
    except KeyboardInterrupt:
        print('\nInterrupted by user.', flush=True)
    finally:
        stop_event.set()
        session.stop(timeout=30.0)
        if input_thread is not None:
            input_thread.join(timeout=1.0)
        status_thread.join(timeout=1.0)
        if signal_ is not None:
            signal_.join(timeout_s=1.0)

    elapsed = time.time() - started_at
    status = session.get_status()
    samples = status.get('samples_written', {})
    print(f'=== ACQ RECORDING STOPPED reason={signal_.reason if signal_ else "user/enter"}'
          f' elapsed={elapsed:.1f}s', flush=True)
    print('\n' + '=' * 70)
    print(f'Session stopped. Data saved to: {session.session_dir}')
    print('=' * 70)
    print(f'=== ACQ DONE dir={session.session_dir} '
          f'samples={" ".join(f"{k}={v}" for k, v in sorted(samples.items()))}',
          flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
