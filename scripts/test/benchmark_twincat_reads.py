"""Compare different ADS read strategies to find the fastest."""
from __future__ import annotations

import time

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

NAMES = [
    'Sir_RunRunShaw_Database.Joint_Angles',
    'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
    'Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame',
    'Sir_RunRunShaw_Database.EndEffector_Orientation',
    'Sir_RunRunShaw_Database.Instrument_Type',
]

DURATION = 5.0


def measure(name: str, fn, warmup=5):
    for _ in range(warmup):
        fn()
    samples = 0
    start = time.perf_counter()
    deadline = start + DURATION
    while time.perf_counter() < deadline:
        fn()
        samples += 1
    total = time.perf_counter() - start
    print(f'{name}: {samples} samples in {total:.3f}s = {samples/total:.1f} Hz, mean={total*1000/samples:.3f}ms')


def main():
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    print(f'Connected, PLC state: {plc.read_state()}\n')

    try:
        # 1. read_list_by_name all arrays
        measure('read_list_by_name(5 arrays)', lambda: plc.read_list_by_name(NAMES))

        # 2. read_list_by_name single largest array
        measure('read_list_by_name(Joint_Angles only)', lambda: plc.read_list_by_name([NAMES[0]]))

        # 3. read_list_by_name positions only
        measure('read_list_by_name(ArmFrame+ScopeFrame)', lambda: plc.read_list_by_name(NAMES[1:3]))

        # 4. read_list_by_name single small array
        measure('read_list_by_name(Instrument_Type only)', lambda: plc.read_list_by_name([NAMES[-1]]))

        # 5. read state (smallest possible ADS operation)
        measure('read_state()', lambda: plc.read_state())

        # 6. get_all_symbols (metadata overhead)
        measure('get_symbolinfo_list()', lambda: plc.get_symbolinfo_list())

    finally:
        plc.close()
        print('\nConnection closed.')


if __name__ == '__main__':
    raise SystemExit(main())
