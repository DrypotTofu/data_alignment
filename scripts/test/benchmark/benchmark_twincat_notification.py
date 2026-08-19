"""Benchmark TwinCAT ADS notification (async subscription) performance."""
from __future__ import annotations

import ctypes
import queue
import time

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

# Symbol to subscribe to (a scalar INT is lightweight).
SYMBOL = 'Sir_RunRunShaw_Database.Instrument_Type'
DURATION = 5.0


def main():
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    print(f'Connected, PLC state: {plc.read_state()}\n')

    try:
        # Get info for the symbol.
        sym = plc.get_symbol(SYMBOL)
        print(f'Symbol: {SYMBOL}')
        print(f'  index_group: {sym.index_group}')
        print(f'  index_offset: {sym.index_offset}')
        print(f'  array_size: {sym.array_size}')

        q: queue.Queue = queue.Queue()

        def callback(handle, timestamp, value):
            # timestamp is from ADS device (if supported), otherwise 0.
            # We use PC arrival time for comparison.
            q.put((time.perf_counter(), timestamp, value))

        # Cycle time in 100ns units. 1ms = 10000.
        attr = pyads.NotificationAttrib(length=sym.array_size, cycle_time=10000)
        notification, user_handle = sym.add_device_notification(callback, attr=attr)
        print(f'Notification handle: {notification}, user_handle={user_handle}, cycle_time=1ms\n')

        time.sleep(DURATION)

        sym.del_device_notification(notification)

        samples = q.qsize()
        print(f'Received {samples} notifications in {DURATION}s = {samples/DURATION:.1f} Hz')

        # Compute inter-arrival intervals.
        arrivals = [item[0] for item in list(q.queue)]
        if len(arrivals) > 1:
            diffs_ms = [(arrivals[i] - arrivals[i-1]) * 1000 for i in range(1, len(arrivals))]
            mean_ms = sum(diffs_ms) / len(diffs_ms)
            min_ms = min(diffs_ms)
            max_ms = max(diffs_ms)
            sorted_diffs = sorted(diffs_ms)
            p99 = sorted_diffs[int(len(sorted_diffs) * 0.99)]
            print(f'Inter-arrival: mean={mean_ms:.3f}ms, min={min_ms:.3f}ms, max={max_ms:.3f}ms, p99={p99:.3f}ms')

    finally:
        plc.close()
        print('\nConnection closed.')


if __name__ == '__main__':
    raise SystemExit(main())
