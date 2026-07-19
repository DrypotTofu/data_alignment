"""Benchmark minimal TwinCAT reads: only pose (position + orientation)."""
from __future__ import annotations

import time

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

# Full set used today.
FULL_SYMBOLS = [
    'Sir_RunRunShaw_Database.Joint_Angles',
    'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
    'Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame',
    'Sir_RunRunShaw_Database.EndEffector_Orientation',
    'Sir_RunRunShaw_Database.Instrument_Type',
]

# Minimal set for VLGP: arm-frame position + orientation of selected arm.
MINIMAL_SYMBOLS = [
    'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
    'Sir_RunRunShaw_Database.EndEffector_Orientation',
]

# Even more minimal: just one arm frame position.
POSITION_ONLY = [
    'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
]

DURATION = 5.0


def measure(plc, name, symbols):
    names = list(symbols)
    # warmup
    for _ in range(5):
        plc.read_list_by_name(names)
    samples = 0
    start = time.perf_counter()
    deadline = start + DURATION
    while time.perf_counter() < deadline:
        plc.read_list_by_name(names)
        samples += 1
    total = time.perf_counter() - start
    print(f'{name}: {samples} samples in {total:.3f}s = {samples/total:.1f} Hz, mean={total*1000/samples:.3f}ms')


def main():
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    print(f'Connected, PLC state: {plc.read_state()}\n')
    try:
        measure(plc, 'full set (5 arrays)', FULL_SYMBOLS)
        measure(plc, 'minimal pose (2 arrays)', MINIMAL_SYMBOLS)
        measure(plc, 'position only (1 array)', POSITION_ONLY)
    finally:
        plc.close()
        print('\nConnection closed.')


if __name__ == '__main__':
    raise SystemExit(main())
