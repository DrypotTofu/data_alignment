"""List TwinCAT PLC symbols that look like timestamps / time axis."""
from __future__ import annotations

import time

import pyads


def main():
    ams_net_id = '169.254.226.99.1.1'
    ams_port = 851
    ip_address = '192.168.22.22'

    plc = pyads.Connection(ams_net_id, ams_port, ip_address)
    plc.open()
    try:
        # Basic task info.
        cycle_time = plc.read_by_name(
            'TwinCAT_SystemInfoVarList._TaskInfo[1].CycleTime',
            plc_datatype=pyads.PLCTYPE_UDINT,
        )
        last_exec = plc.read_by_name(
            'TwinCAT_SystemInfoVarList._TaskInfo[1].LastExecTime',
            plc_datatype=pyads.PLCTYPE_UDINT,
        )
        print(f'PLC Task CycleTime = {cycle_time} (100ns units) = {cycle_time / 10:.0f} us = {cycle_time / 10000:.3f} ms')
        print(f'PLC Task LastExecTime = {last_exec} (100ns units) = {last_exec / 10:.0f} us')

        # Poll CycleCount to see if it increments every PLC cycle.
        print('\n=== CycleCount stability check (1000 samples) ===')
        counts = []
        pc_times_s = []
        for _ in range(1000):
            t0 = time.perf_counter()
            c = plc.read_by_name(
                'TwinCAT_SystemInfoVarList._TaskInfo[1].CycleCount',
                plc_datatype=pyads.PLCTYPE_UDINT,
            )
            t1 = time.perf_counter()
            counts.append(c)
            pc_times_s.append(t0)

        diffs = [counts[i] - counts[i - 1] for i in range(1, len(counts))]
        print(f'Samples: {len(counts)}')
        print(f'CycleCount range: {counts[0]} -> {counts[-1]}')
        print(f'Diff min/mean/max: {min(diffs)}, {sum(diffs)/len(diffs):.2f}, {max(diffs)}')
        print(f'Unique diffs: {sorted(set(diffs))[:10]} ...')

        # Estimate real elapsed PLC time vs PC wall time.
        elapsed_pc_ms = (pc_times_s[-1] - pc_times_s[0]) * 1000
        elapsed_plc_ms = (counts[-1] - counts[0]) * cycle_time / 10000
        print(f'\nPC elapsed: {elapsed_pc_ms:.3f} ms')
        print(f'PLC elapsed (from CycleCount): {elapsed_plc_ms:.3f} ms')
        print(f'Drift: {elapsed_pc_ms - elapsed_plc_ms:.3f} ms ({((elapsed_pc_ms - elapsed_plc_ms) / elapsed_plc_ms) * 100:.4f}%)')

        # Also check a simultaneous read with pose data.
        print('\n=== Simultaneous read: pose + CycleCount ===')
        names = [
            'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
            'TwinCAT_SystemInfoVarList._TaskInfo[1].CycleCount',
        ]
        raw = plc.read_list_by_name(names)
        print(raw)
    finally:
        plc.close()


if __name__ == '__main__':
    main()
