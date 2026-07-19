"""Benchmark raw ADS reads using pre-resolved index group/offset."""
from __future__ import annotations

import time

import pyads


AMS_NET_ID = '169.254.226.99.1.1'
AMS_PORT = 851
IP_ADDRESS = '192.168.22.22'

SYMBOLS = {
    'joint_angles': 'Sir_RunRunShaw_Database.Joint_Angles',
    'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
    'scopeframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame',
    'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
    'instrument_types': 'Sir_RunRunShaw_Database.Instrument_Type',
}

DURATION = 5.0


def get_symbol_info(plc, name):
    sym = plc.get_symbol(name)
    return sym.index_group, sym.index_offset, sym.array_size


def measure_list_by_name(plc, duration):
    names = list(SYMBOLS.values())
    for _ in range(5):
        plc.read_list_by_name(names)
    samples = 0
    start = time.perf_counter()
    deadline = start + duration
    while time.perf_counter() < deadline:
        plc.read_list_by_name(names)
        samples += 1
    total = time.perf_counter() - start
    print(f'read_list_by_name: {samples} samples, {samples/total:.1f} Hz, mean={total*1000/samples:.3f}ms')


def measure_raw_reads(plc, infos, duration):
    for _ in range(5):
        for ig, io, size, plc_type in infos:
            plc.read(ig, io, plc_type)
    samples = 0
    start = time.perf_counter()
    deadline = start + duration
    while time.perf_counter() < deadline:
        for ig, io, size, plc_type in infos:
            plc.read(ig, io, plc_type)
        samples += 1
    total = time.perf_counter() - start
    print(f'raw read (5 separate): {samples} samples, {samples/total:.1f} Hz, mean={total*1000/samples:.3f}ms')


def measure_raw_single_block(plc, infos, duration):
    """Read all symbols in one contiguous block if they are contiguous."""
    # This is a hack: we read from the first symbol offset for the total length.
    # May not work if symbols are not contiguous in memory.
    first_ig, first_io, first_size, _ = infos[0]
    total_size = sum(size for _, _, size, _ in infos)
    for _ in range(5):
        plc.read(first_ig, first_io, pyads.PLCTYPE_ARR_LREAL(total_size // 8))
    samples = 0
    start = time.perf_counter()
    deadline = start + duration
    while time.perf_counter() < deadline:
        plc.read(first_ig, first_io, pyads.PLCTYPE_ARR_LREAL(total_size // 8))
        samples += 1
    total = time.perf_counter() - start
    print(f'raw single block ({total_size} bytes): {samples} samples, {samples/total:.1f} Hz, mean={total*1000/samples:.3f}ms')


def main():
    plc = pyads.Connection(AMS_NET_ID, AMS_PORT, IP_ADDRESS)
    plc.open()
    print(f'Connected, PLC state: {plc.read_state()}\n')

    try:
        measure_list_by_name(plc, DURATION)

        infos = []
        for key, name in SYMBOLS.items():
            ig, io, size = get_symbol_info(plc, name)
            print(f'{name}: ig={ig}, io={io}, size={size}')
            # Determine PLC type from size (all are LREAL arrays except INT[4]).
            if key == 'instrument_types':
                plc_type = pyads.PLCTYPE_ARR_INT(size // 2)
            else:
                plc_type = pyads.PLCTYPE_ARR_LREAL(size // 8)
            infos.append((ig, io, size, plc_type))

        print()
        measure_raw_reads(plc, infos, DURATION)

        # Only try single block if all symbols share the same index_group.
        if len(set(ig for ig, _, _, _ in infos)) == 1:
            try:
                measure_raw_single_block(plc, infos, DURATION)
            except Exception as e:
                print(f'raw single block failed: {e}')
        else:
            print('Symbols are not in same index group, skipping single block read')

    finally:
        plc.close()
        print('\nConnection closed.')


if __name__ == '__main__':
    raise SystemExit(main())
