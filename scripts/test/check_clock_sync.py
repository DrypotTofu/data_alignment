"""Estimate clock offset between the PC and a TwinCAT PLC.

This script reads TwinCAT's timestamp symbol (e.g. MAIN.timestamp_ns) via ADS
and compares it with the PC's CLOCK_REALTIME. It is useful for verifying
NTP/PTP synchronization or for measuring ADS communication latency.

Usage:
    python check_clock_sync.py --ams-net-id 192.168.1.100.1.1 --symbol MAIN.timestamp_ns
    python check_clock_sync.py --ams-net-id 192.168.1.100.1.1 --symbol MAIN.timestamp_ns --samples 100
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

# Allow importing from the parent scripts package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pyads
except OSError as e:
    if 'TcAdsDll.dll' in str(e):
        pyads = None
    else:
        raise

from acquisition.timestamp import filetime_to_ns

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def read_twincat_timestamp(conn: pyads.Connection, symbol: str,
                           timestamp_is_filetime: bool = False) -> int | None:
    """Read TwinCAT timestamp and convert to Unix-epoch nanoseconds."""
    try:
        value = conn.read_by_name(symbol)
        if value is None:
            return None
        ts = int(value)
        if timestamp_is_filetime:
            return filetime_to_ns(ts)
        return ts
    except Exception as e:
        logger.error('Failed to read %s: %s', symbol, e)
        return None


def measure_offset(ams_net_id: str, ams_port: int, symbol: str,
                   samples: int = 30, timestamp_is_filetime: bool = False) -> None:
    if pyads is None:
        logger.error('pyads could not load TcAdsDll.dll.')
        logger.error('This usually means TwinCAT is not installed on this PC.')
        logger.error('Run this script on a PC that has TwinCAT ADS runtime installed,')
        logger.error('or install TwinCAT XAE / ADS driver on this machine.')
        return

    logger.info('Connecting to TwinCAT %s:%d ...', ams_net_id, ams_port)
    conn = pyads.Connection(ams_net_id, ams_port)
    try:
        conn.open()
    except Exception as e:
        logger.error('Could not open ADS connection: %s', e)
        logger.error('Check that:')
        logger.error('  1. TwinCAT is running and PLC is in Run mode')
        logger.error('  2. ADS route is configured on the TwinCAT host')
        logger.error('  3. This PC can reach TwinCAT on port %d', ams_port)
        return

    logger.info('Reading %d samples from %s ...', samples, symbol)
    offsets = []
    rtts = []

    for i in range(samples):
        t0 = time.time_ns()
        tc_ts = read_twincat_timestamp(conn, symbol, timestamp_is_filetime)
        t1 = time.time_ns()

        if tc_ts is None:
            continue

        pc_ts = (t0 + t1) // 2  # midpoint of request/response
        rtt = (t1 - t0) / 1e6   # ms
        offset = pc_ts - tc_ts  # ns, positive means PC ahead of TwinCAT

        offsets.append(offset / 1e6)  # convert to ms
        rtts.append(rtt)
        logger.info('[%3d] TwinCAT=%d | PC=%d | offset=%+.3f ms | RTT=%.3f ms',
                    i, tc_ts, pc_ts, offset / 1e6, rtt)

        # Small sleep to avoid flooding ADS.
        time.sleep(0.02)

    conn.close()

    if not offsets:
        logger.error('No valid samples collected.')
        return

    mean_offset = statistics.mean(offsets)
    std_offset = statistics.stdev(offsets) if len(offsets) > 1 else 0.0
    mean_rtt = statistics.mean(rtts)

    logger.info('')
    logger.info('--- Summary ---')
    logger.info('Samples    : %d', len(offsets))
    logger.info('Mean offset: %+.3f ms (PC - TwinCAT)', mean_offset)
    logger.info('Std offset : %.3f ms', std_offset)
    logger.info('Min offset : %+.3f ms', min(offsets))
    logger.info('Max offset : %+.3f ms', max(offsets))
    logger.info('Mean RTT   : %.3f ms', mean_rtt)

    if abs(mean_offset) < 1.0 and std_offset < 1.0:
        logger.info('Clocks are well synchronized (< 1 ms).')
    elif abs(mean_offset) < 10.0:
        logger.warning('Clocks are loosely synchronized (1~10 ms).')
    else:
        logger.warning('Clocks are NOT synchronized (> 10 ms). Configure NTP/PTP.')


def main():
    parser = argparse.ArgumentParser(description='Check PC vs TwinCAT clock synchronization.')
    parser.add_argument('--ams-net-id', type=str, default='192.168.1.100.1.1',
                        help='TwinCAT AMS Net ID (default: 192.168.1.100.1.1)')
    parser.add_argument('--ams-port', type=int, default=851,
                        help='TwinCAT AMS port (default: 851)')
    parser.add_argument('--symbol', type=str, default='MAIN.timestamp_ns',
                        help='PLC timestamp symbol in nanoseconds (default: MAIN.timestamp_ns)')
    parser.add_argument('--filetime', action='store_true',
                        help='Set if the PLC timestamp is a Windows FILETIME instead of ns')
    parser.add_argument('--samples', type=int, default=30,
                        help='Number of samples to collect (default: 30)')
    args = parser.parse_args()

    measure_offset(args.ams_net_id, args.ams_port, args.symbol,
                   args.samples, args.filetime)


if __name__ == '__main__':
    main()
