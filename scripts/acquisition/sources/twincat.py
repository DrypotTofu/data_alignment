"""TwinCAT ADS acquisition source.

This source connects to a TwinCAT PLC/runtime over ADS and reads robot pose
and joint-angle arrays. It is currently configured for the dVRK-style robot
controller at:

    AMS NetID : 169.254.226.99.1.1
    IP        : 192.168.22.22
    ADS port  : 851

Expected PLC symbols
--------------------
The robot exposes the following array variables (verified against the existing
Scope project ``TwinCATScope/TwinCAT Project1``):

    Sir_RunRunShaw_Database.Joint_Angles[4][12]              : LREAL
    Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame[4][3] : LREAL
    Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame[4][3] : LREAL
    Sir_RunRunShaw_Database.EndEffector_Orientation[4][4]    : LREAL
    Sir_RunRunShaw_Database.Instrument_Type[4]               : INT

TwinCAT ADS route on the PC side
--------------------------------
Add a static route in ``C:\TwinCAT\3.1\Target\StaticRoutes.xml``:

    <Route>
        <Name>RobotController</Name>
        <Address>192.168.22.22</Address>
        <NetId>169.254.226.99.1.1</NetId>
        <Type>TCP_IP</Type>
        <Flags>0</Flags>
    </Route>

Python side (pyads)
-------------------
    plc = pyads.Connection('169.254.226.99.1.1', 851, '192.168.22.22')
    plc.open()
    values = plc.read_list_by_name([
        'Sir_RunRunShaw_Database.Joint_Angles',
        'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
        'Sir_RunRunShaw_Database.EndEffector_Orientation',
    ])
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


def _reshape(flat: list[Any], shape: tuple[int, ...]) -> list[Any]:
    """Reshape a flat ADS array read into nested Python lists.

    TwinCAT returns multi-dimensional arrays in row-major order. This helper
    mirrors that layout so ``[4, 12]`` becomes a list of 4 arms each with 12
    joints.
    """
    if len(shape) == 1:
        return list(flat)
    if len(shape) == 2:
        rows, cols = shape
        return [list(flat[i * cols:(i + 1) * cols]) for i in range(rows)]
    raise NotImplementedError(f'Unsupported array shape: {shape}')


@dataclass
class TwinCATConfig:
    """Configuration for TwinCAT ADS source."""

    topic_name: str = 'robot/pose'
    ams_net_id: str = '169.254.226.99.1.1'
    ams_port: int = 851  # TC3 default
    ip_address: str = '192.168.22.22'
    local_ams_net_id: str | None = None
    cycle_period_ms: float = 1.0  # 1 kHz default
    symbols: dict[str, str] | None = None
    symbol_shapes: dict[str, tuple[int, ...] | list[int]] | None = None
    selected_arm: int = 1  # which arm (1..4) to record as the primary pose
    # How to timestamp TwinCAT samples:
    #   'twincat'    -> read a PLC timestamp symbol (not available on this robot)
    #   'pc_arrival' -> use PC time when ADS response arrives (recommended)
    #   'pc_cycle'   -> use PC time at start of each read cycle
    timestamp_mode: str = 'pc_arrival'

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = {
                'joint_angles': 'Sir_RunRunShaw_Database.Joint_Angles',
                'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
                'scopeframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame',
                'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
                'instrument_types': 'Sir_RunRunShaw_Database.Instrument_Type',
            }
        if self.symbol_shapes is None:
            self.symbol_shapes = {
                'joint_angles': (4, 12),
                'armframe_positions': (4, 3),
                'scopeframe_positions': (4, 3),
                'orientations': (4, 4),
                'instrument_types': (4,),
            }
        # Normalise list shapes from YAML to tuples internally.
        self.symbol_shapes = {
            k: tuple(v) for k, v in self.symbol_shapes.items()
        }


class TwinCATSource(DataSource):
    """TwinCAT ADS source that reads robot joint angles and end-effector pose.

    The source runs at the configured cycle period, reads all arm arrays in a
    single ``read_list_by_name`` call, extracts the selected arm, and publishes
    a flat scalar sample that is easy to store in CSV.
    """

    def __init__(self, config: TwinCATConfig | None = None,
                 clock: UnifiedTimestamp | None = None,
                 output_queue: Any = None):
        super().__init__(clock=clock, output_queue=output_queue)
        self.config = config or TwinCATConfig()
        self._missed_cycles = 0
        self._consecutive_errors = 0
        self._samples_read = 0

    @property
    def name(self) -> str:
        return self.config.topic_name.replace('/', '_')

    def _read_arrays(self, plc) -> dict[str, list[Any]]:
        """Read all configured ADS symbols and reshape them."""
        names = list(self.config.symbols.values())
        raw = plc.read_list_by_name(names)
        out: dict[str, list[Any]] = {}
        for key, ads_name in self.config.symbols.items():
            shape = self.config.symbol_shapes[key]
            out[key] = _reshape(raw[ads_name], shape)
        return out

    def _build_sample(self, cycle_start_ns: int, arrays: dict[str, list[Any]]) -> DataSample:
        """Construct a DataSample for the selected arm."""
        arm_idx = self.config.selected_arm - 1
        joints = arrays['joint_angles'][arm_idx]
        arm_pos = arrays['armframe_positions'][arm_idx]
        scope_pos = arrays['scopeframe_positions'][arm_idx]
        orient = arrays['orientations'][arm_idx]
        inst_type = arrays['instrument_types'][arm_idx]

        if self.config.timestamp_mode == 'pc_arrival':
            sample_ts_ns = self.clock.now_ns()
        elif self.config.timestamp_mode == 'pc_cycle':
            sample_ts_ns = cycle_start_ns
        else:  # 'twincat' fallback when no PLC timestamp symbol exists
            sample_ts_ns = cycle_start_ns

        data: dict[str, Any] = {
            'arm': self.config.selected_arm,
            'instrument_type': inst_type,
            'position_x': arm_pos[0],
            'position_y': arm_pos[1],
            'position_z': arm_pos[2],
            'orientation_w': orient[0],
            'orientation_x': orient[1],
            'orientation_y': orient[2],
            'orientation_z': orient[3],
            'scope_position_x': scope_pos[0],
            'scope_position_y': scope_pos[1],
            'scope_position_z': scope_pos[2],
        }
        for i, v in enumerate(joints, start=1):
            data[f'joint_{i}'] = v

        metadata = {
            'timestamp_mode': self.config.timestamp_mode,
            'ams_net_id': self.config.ams_net_id,
            'ams_port': self.config.ams_port,
            'ip_address': self.config.ip_address,
            'selected_arm': self.config.selected_arm,
            'orientation_order': 'wxyz (to be confirmed)',
            'pc_cycle_start_ns': cycle_start_ns,
        }

        return DataSample(
            timestamp_ns=sample_ts_ns,
            source_name=self.name,
            data=data,
            metadata=metadata,
        )

    def _acquire_loop(self) -> None:
        import pyads

        period_s = self.config.cycle_period_ms / 1000.0
        plc: pyads.Connection | None = None

        while not self._stop_event.is_set():
            # (Re)open ADS connection if needed.
            if plc is None:
                try:
                    plc = pyads.Connection(
                        self.config.ams_net_id,
                        self.config.ams_port,
                        self.config.ip_address,
                    )
                    plc.open()
                    logger.info(
                        'TwinCAT ADS connected to %s:%d via %s',
                        self.config.ams_net_id,
                        self.config.ams_port,
                        self.config.ip_address,
                    )
                    self._consecutive_errors = 0
                except Exception as exc:
                    self._consecutive_errors += 1
                    logger.error(
                        'Failed to open ADS connection (attempt %d): %s',
                        self._consecutive_errors,
                        exc,
                    )
                    time.sleep(min(period_s * 5, 1.0))
                    continue

            cycle_start_ns = self.clock.now_ns()
            try:
                arrays = self._read_arrays(plc)
                sample = self._build_sample(cycle_start_ns, arrays)
                self._put_sample(sample)
                self._samples_read += 1
                self._consecutive_errors = 0
            except Exception as exc:
                self._consecutive_errors += 1
                self._missed_cycles += 1
                logger.error(
                    'ADS read error (consecutive %d): %s',
                    self._consecutive_errors,
                    exc,
                )
                try:
                    plc.close()
                except Exception:
                    pass
                plc = None
                time.sleep(min(period_s * 5, 1.0))
                continue

            # Sleep until next cycle, guarding against overshoot.
            elapsed_s = (self.clock.now_ns() - cycle_start_ns) / 1e9
            sleep_s = period_s - elapsed_s
            if sleep_s > 0:
                time.sleep(sleep_s)

        if plc is not None:
            try:
                plc.close()
            except Exception as exc:
                logger.warning('Error closing ADS connection: %s', exc)

    def get_summary(self) -> dict[str, Any]:
        summary = super().get_summary()
        summary.update({
            'topic_name': self.config.topic_name,
            'ams_net_id': self.config.ams_net_id,
            'ams_port': self.config.ams_port,
            'ip_address': self.config.ip_address,
            'cycle_period_ms': self.config.cycle_period_ms,
            'symbols': self.config.symbols,
            'symbol_shapes': self.config.symbol_shapes,
            'selected_arm': self.config.selected_arm,
            'timestamp_mode': self.config.timestamp_mode,
            'samples_read': self._samples_read,
            'missed_cycles': self._missed_cycles,
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
