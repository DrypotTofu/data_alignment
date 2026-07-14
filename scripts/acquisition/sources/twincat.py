"""TwinCAT ADS acquisition source placeholder.

Expected TwinCAT PLC interface
------------------------------
To integrate this source you should expose the following variables in the
TwinCAT MAIN program and enable "Create symbols" on `PlcTask`:

    // Robot end-effector pose (Cartesian)
    robot_pose : ARRAY[0..6] OF LREAL;   // x, y, z, qw, qx, qy, qz

    // Joint angles (optional, for joint-space logging/recording)
    joint_angles : ARRAY[0..6] OF LREAL; // adjust DOF to your robot

    // Gripper / instrument state (optional)
    jaw_angle : LREAL;
    instrument_type : INT;

    // Unified timestamp: TwinCAT current time in nanoseconds since Unix epoch.
    // Use FB_LocalSystemTime or TOF to fill this in the PLC task.
    timestamp_ns : ULINT;

    // A heartbeat counter incremented every PLC cycle; useful to detect
    // dropped ADS reads or stale data.
    heartbeat : ULINT;

TwinCAT ADS route on the PC side
--------------------------------
Add a static route in `C:\TwinCAT\3.1\Target\StaticRoutes.xml`:

    <Route>
        <Name>AcquisitionPC</Name>
        <Address>192.168.1.50</Address>
        <NetId>192.168.1.50.1.1</NetId>
        <Type>TCP_IP</Type>
        <Flags>0</Flags>
    </Route>

Python side (pyads)
-------------------
    plc = pyads.Connection('192.168.1.100.1.1', 851)
    plc.open()
    pose = plc.read_by_name('MAIN.robot_pose')
    ts   = plc.read_by_name('MAIN.timestamp_ns')
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base_source import DataSample, DataSource
from ..timestamp import UnifiedTimestamp

logger = logging.getLogger(__name__)


@dataclass
class TwinCATConfig:
    """Configuration for TwinCAT ADS source."""
    topic_name: str = 'robot/pose'
    ams_net_id: str = '192.168.1.100.1.1'
    ams_port: int = 851  # TC3 default
    ip_address: str = '192.168.1.100'
    local_ams_net_id: str | None = None
    cycle_period_ms: float = 1.0  # 1 kHz default
    symbols: dict[str, str] | None = None
    # How to timestamp TwinCAT samples:
    #   'twincat'    -> use MAIN.timestamp_ns from PLC (recommended)
    #   'pc_arrival' -> use PC time when ADS response arrives
    #   'pc_cycle'   -> use PC time at start of each cycle
    timestamp_mode: str = 'twincat'

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = {
                'robot_pose': 'MAIN.robot_pose',
                'joint_angles': 'MAIN.joint_angles',
                'jaw_angle': 'MAIN.jaw_angle',
                'instrument_type': 'MAIN.instrument_type',
                'timestamp_ns': 'MAIN.timestamp_ns',
                'heartbeat': 'MAIN.heartbeat',
            }


class TwinCATSource(DataSource):
    """Stub TwinCAT ADS source.

    Replace `_acquire_loop()` with real `pyads` reads once the PLC symbols are
    available. The source is designed to run at the configured cycle period and
    publish pose samples to the Session queue.
    """

    def __init__(self, config: TwinCATConfig | None = None,
                 clock: UnifiedTimestamp | None = None,
                 output_queue: Any = None):
        super().__init__(clock=clock, output_queue=output_queue)
        self.config = config or TwinCATConfig()
        self._last_heartbeat: int | None = None
        self._missed_cycles = 0
        self._twincat_to_pc_offset_ns: int | None = None

    @property
    def name(self) -> str:
        return self.config.topic_name.replace('/', '_')

    def _acquire_loop(self) -> None:
        """Stub TwinCAT acquisition loop.

        In production this should use pyads to read the PLC variables. The
        timestamp strategy depends on `timestamp_mode`:

        1. 'twincat' (recommended):
           Read `MAIN.timestamp_ns` from the PLC and use it as the sample
           timestamp. This is the most accurate because it reflects when the
           data was produced in the real-time controller, not when it arrived
           on the PC. To align it with the PC clock domain, compute:
               offset_ns = pc_receive_ns - twincat_timestamp_ns
           and store this offset in metadata.

        2. 'pc_arrival':
           Use `self.clock.now_ns()` when the ADS response is received.
           Simple, but includes variable network/ADS latency (typically
           0.5~5 ms on a local network).

        3. 'pc_cycle':
           Use `self.clock.now_ns()` at the start of each read cycle.
           Less noisy than 'pc_arrival' but still tied to PC scheduling.
        """
        logger.warning(
            'TwinCATSource is a placeholder. Configure AMS NetId=%s, port=%d '
            'and implement pyads reads before recording robot data.',
            self.config.ams_net_id, self.config.ams_port
        )
        period_s = self.config.cycle_period_ms / 1000.0
        while not self._stop_event.is_set():
            cycle_start_ns = self.clock.now_ns()

            # Stub values. Replace with real pyads reads:
            #   twincat_ts_ns = plc.read_by_name(self.config.symbols['timestamp_ns'])
            #   pose          = plc.read_by_name(self.config.symbols['robot_pose'])
            #   joints        = plc.read_by_name(self.config.symbols['joint_angles'])
            #   heartbeat     = plc.read_by_name(self.config.symbols['heartbeat'])
            twincat_ts_ns = cycle_start_ns  # placeholder
            pose = [0.0] * 7
            joints = [0.0] * 7
            heartbeat = 0

            if self.config.timestamp_mode == 'twincat':
                sample_ts_ns = twincat_ts_ns
                if self._twincat_to_pc_offset_ns is None:
                    self._twincat_to_pc_offset_ns = cycle_start_ns - twincat_ts_ns
            elif self.config.timestamp_mode == 'pc_arrival':
                sample_ts_ns = self.clock.now_ns()
            else:  # 'pc_cycle'
                sample_ts_ns = cycle_start_ns

            sample = DataSample(
                timestamp_ns=sample_ts_ns,
                source_name=self.name,
                data={
                    'pose': pose,
                    'joints': joints,
                },
                metadata={
                    'timestamp_mode': self.config.timestamp_mode,
                    'twincat_timestamp_ns': twincat_ts_ns,
                    'pc_receive_ns': cycle_start_ns,
                    'twincat_to_pc_offset_ns': self._twincat_to_pc_offset_ns,
                    'heartbeat': heartbeat,
                }
            )
            self._put_sample(sample)
            time.sleep(period_s)

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary.update({
            'topic_name': self.config.topic_name,
            'ams_net_id': self.config.ams_net_id,
            'ams_port': self.config.ams_port,
            'ip_address': self.config.ip_address,
            'cycle_period_ms': self.config.cycle_period_ms,
            'symbols': self.config.symbols,
            'timestamp_mode': self.config.timestamp_mode,
            'twincat_to_pc_offset_ns': self._twincat_to_pc_offset_ns,
        })
        return summary


class TwinCATWriter:
    """Writer for TwinCAT pose samples."""

    def __init__(self, source_name: str, out_dir: Path):
        self.source_name = source_name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.out_dir / 'twincat_poses.csv'
        self._f = open(self.csv_path, 'w', newline='')
        # Header will be written on first sample.
        self._writer = None

    def write(self, sample: DataSample) -> None:
        import csv
        data = sample.data
        row = {
            'timestamp_ns': sample.timestamp_ns,
            **data,
            **sample.metadata,
        }
        if self._writer is None:
            self._writer = csv.DictWriter(self._f, fieldnames=list(row.keys()))
            self._writer.writeheader()
        self._writer.writerow(row)

    def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
