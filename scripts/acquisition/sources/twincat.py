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
    Sir_RunRunShaw_Database.Gripper_Angle[4]                : LREAL
    Sir_RunRunShaw_Database.Gripper_Signal[4]               : LREAL
    Sir_RunRunShaw_Database.EnergyInstrument_Status[4]      : BOOL

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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyads

from ..base_source import DataSample, DataSource
from ..timestamp import TimestampSource, UnifiedTimestamp

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
    #   'twincat'    -> use PLC task CycleCount * CycleTime (stable PLC time axis)
    #   'pc_arrival' -> use PC time when ADS response arrives (recommended)
    #   'pc_cycle'   -> use PC time at start of each read cycle
    timestamp_mode: str = 'pc_arrival'
    # PLC symbol that exposes the task cycle counter. On this robot the
    # standard TwinCAT system info variable '_TaskInfo[1].CycleCount' is
    # available and increments once per PLC task cycle (1 ms).
    twincat_timestamp_symbol: str = (
        'TwinCAT_SystemInfoVarList._TaskInfo[1].CycleCount'
    )
    # PLC symbol that exposes the task cycle time in 100 ns units. Used once
    # at startup to convert CycleCount into nanoseconds.
    twincat_timestamp_cycle_time_symbol: str = (
        'TwinCAT_SystemInfoVarList._TaskInfo[1].CycleTime'
    )
    # Optional override for CycleCount -> time conversion. If set, the source
    # skips reading the CycleTime symbol. 1 kHz task -> 1.0 cycles/ms.
    twincat_timestamp_cycles_per_ms: float | None = None
    # When the session clock_source is 'twincat_master', the source estimates
    # host->TwinCAT offset from each 'twincat' sample and updates the shared
    # clock offset. This alpha controls an exponential moving average
    # (0 = disable update, 1 = no smoothing). Default 0.05 gives a stable
    # offset while still tracking slow drift.
    twincat_master_sync_alpha: float = 0.05
    # If True, measure the ADS request/response RTT around each read and use
    # t_recv - RTT/2 as the PC arrival time. This reduces short-term jitter
    # in the host->TwinCAT offset estimate caused by variable ADS latency.
    use_rtt_correction: bool = False
    # If True, include both PC-axis and PLC-axis timestamps in the sample
    # metadata as 'timestamp_ns_pc' and 'timestamp_ns_plc'. The main
    # 'timestamp_ns' field still follows timestamp_mode for compatibility.
    output_dual_timestamps: bool = False
    # Number of parallel ADS connections to open. Each connection runs in its
    # own thread and polls the same symbols. The resulting samples are merged
    # into the output queue, increasing effective sample rate at the cost of
    # duplicated CycleCounts. Default 1 preserves the original behaviour.
    num_connections: int = 1
    # If True, output one sample per arm (1..4) per read cycle. Each arm is
    # written to its own CSV under robot_pose_arm<N>/twincat_poses.csv. This
    # allows collecting all arms and all configured symbols over a single ADS
    # connection. When False, only selected_arm is output (original behaviour).
    output_all_arms: bool = False

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = {
                'joint_angles': 'Sir_RunRunShaw_Database.Joint_Angles',
                'armframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ArmFrame',
                'scopeframe_positions': 'Sir_RunRunShaw_Database.EndEffector_Coordinate_ScopeFrame',
                'orientations': 'Sir_RunRunShaw_Database.EndEffector_Orientation',
                'instrument_types': 'Sir_RunRunShaw_Database.Instrument_Type',
                'gripper_angle': 'Sir_RunRunShaw_Database.Gripper_Angle',
                'gripper_signal': 'Sir_RunRunShaw_Database.Gripper_Signal',
                'energy_instrument_status': 'Sir_RunRunShaw_Database.EnergyInstrument_Status',
            }
        if self.symbol_shapes is None:
            self.symbol_shapes = {
                'joint_angles': (4, 12),
                'armframe_positions': (4, 3),
                'scopeframe_positions': (4, 3),
                'orientations': (4, 4),
                'instrument_types': (4,),
                'gripper_angle': (4,),
                'gripper_signal': (4,),
                'energy_instrument_status': (4,),
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
        # Cached PLC cycle time in nanoseconds; populated on first read when
        # timestamp_mode == 'twincat' and no override is configured.
        self._twincat_cycle_time_ns: int | None = None
        # Protects counters and the shared master clock offset update when
        # multiple ADS connections run in parallel.
        self._lock = threading.Lock()
        self._worker_threads: list[threading.Thread] = []
        self._connections: list[pyads.Connection] = []

    @property
    def name(self) -> str:
        return self.config.topic_name.replace('/', '_')

    def _read_arrays(self, plc) -> dict[str, Any]:
        """Read all configured ADS symbols and reshape them."""
        names = list(self.config.symbols.values())
        if self.config.timestamp_mode == 'twincat' or self.config.output_dual_timestamps:
            names.append(self.config.twincat_timestamp_symbol)
        raw = plc.read_list_by_name(names)
        out: dict[str, Any] = {}
        for key, ads_name in self.config.symbols.items():
            shape = self.config.symbol_shapes[key]
            out[key] = _reshape(raw[ads_name], shape)
        if self.config.timestamp_mode == 'twincat' or self.config.output_dual_timestamps:
            out['__twincat_cycle_count'] = raw[self.config.twincat_timestamp_symbol]
        return out

    def _ensure_twincat_cycle_time_ns(self, plc) -> int:
        """Return (and cache) the PLC task cycle time in nanoseconds."""
        if self._twincat_cycle_time_ns is not None:
            return self._twincat_cycle_time_ns
        if self.config.twincat_timestamp_cycles_per_ms is not None:
            self._twincat_cycle_time_ns = int(1_000_000.0 / self.config.twincat_timestamp_cycles_per_ms)
        else:
            raw = plc.read_by_name(
                self.config.twincat_timestamp_cycle_time_symbol,
                plc_datatype=pyads.PLCTYPE_UDINT,
            )
            # TwinCAT CycleTime is expressed in 100 ns units.
            self._twincat_cycle_time_ns = int(raw) * 100
        return self._twincat_cycle_time_ns

    def _update_twincat_master_offset(
        self,
        twincat_ts_ns: int,
        pc_arrival_ns: int,
        conn_id: int = 0,
    ) -> None:
        """Update the shared clock offset when TwinCAT is the master clock.

        This makes camera frame timestamps (which call ``self.clock.now_ns()``)
        land on the same PLC time axis as the TwinCAT samples.

        Only connection 0 performs the update to avoid redundant work and
        reduce lock contention when multiple connections are active.
        """
        if self.clock.config.source != TimestampSource.TWINCAT_MASTER:
            return
        if conn_id != 0:
            return
        alpha = self.config.twincat_master_sync_alpha
        instant_offset = twincat_ts_ns - pc_arrival_ns
        with self._lock:
            current_offset = self.clock.config.offset_ns
            if alpha <= 0.0:
                self.clock.config.offset_ns = instant_offset
            else:
                self.clock.config.offset_ns = int(
                    alpha * instant_offset + (1.0 - alpha) * current_offset
                )

    def _build_sample(
        self,
        cycle_start_ns: int,
        arrays: dict[str, Any],
        plc: pyads.Connection,
        t_send_ns: int,
        t_recv_ns: int,
        conn_id: int = 0,
        arm_idx: int | None = None,
    ) -> DataSample:
        """Construct a DataSample for a specific arm.

        Only the symbols configured in ``self.config.symbols`` are required;
        missing fields are omitted from the sample. This allows users to read
        only the pose subset (position + orientation) for higher ADS rates.

        ``cycle_start_ns`` and the internally captured ``pc_arrival_ns`` are
        raw host PC wall times (``time.time_ns()``). The final
        ``timestamp_ns`` is expressed in the configured master clock domain:
        when ``clock_source == 'twincat_master'`` the source adds the shared
        offset so that video frames and TwinCAT poses share the PLC time axis.

        ``t_send_ns`` and ``t_recv_ns`` bracket the ADS read; when RTT
        correction is enabled, ``pc_arrival_ns`` is approximated as
        ``t_recv_ns - RTT/2`` to reduce offset-estimate jitter.

        Args:
            arm_idx: Zero-based arm index. If None, uses ``selected_arm - 1``.
        """
        if arm_idx is None:
            arm_idx = self.config.selected_arm - 1
        arm_id = arm_idx + 1
        has = {k: k in arrays for k in [
            'joint_angles', 'armframe_positions', 'scopeframe_positions',
            'orientations', 'instrument_types',
            'gripper_angle', 'gripper_signal', 'energy_instrument_status']}

        rtt_ns = t_recv_ns - t_send_ns
        # Raw PC wall time at response arrival. Kept raw in metadata so post-
        # processing can see network/ADS latency.
        pc_arrival_ns = t_recv_ns
        if self.config.use_rtt_correction:
            # Approximate the PC time at which the PLC sample was taken,
            # assuming symmetric request/response latency.
            pc_arrival_ns = t_recv_ns - rtt_ns // 2

        master_offset_ns = self.clock.config.offset_ns

        # Compute the PLC-axis timestamp whenever CycleCount is available.
        timestamp_ns_plc: int | None = None
        if '__twincat_cycle_count' in arrays:
            cycle_count = arrays['__twincat_cycle_count']
            cycle_time_ns = self._ensure_twincat_cycle_time_ns(plc)
            timestamp_ns_plc = int(cycle_count) * cycle_time_ns

        # Raw PC-axis timestamp. For pc_cycle this is the cycle start; otherwise
        # it is the (possibly RTT-corrected) response arrival time.
        if self.config.timestamp_mode == 'pc_cycle':
            timestamp_ns_pc = cycle_start_ns
        else:
            timestamp_ns_pc = pc_arrival_ns

        # Main timestamp follows the configured mode for backward compatibility.
        if self.config.timestamp_mode == 'pc_arrival':
            sample_ts_ns = timestamp_ns_pc + master_offset_ns
        elif self.config.timestamp_mode == 'pc_cycle':
            sample_ts_ns = timestamp_ns_pc + master_offset_ns
        elif self.config.timestamp_mode == 'twincat':
            sample_ts_ns = timestamp_ns_plc
            self._update_twincat_master_offset(sample_ts_ns, pc_arrival_ns, conn_id)
        else:
            raise ValueError(
                f"Unsupported timestamp_mode: {self.config.timestamp_mode}"
            )

        data: dict[str, Any] = {'arm': arm_id}

        if has['instrument_types']:
            data['instrument_type'] = arrays['instrument_types'][arm_idx]

        if has['armframe_positions']:
            arm_pos = arrays['armframe_positions'][arm_idx]
            data['position_x'] = arm_pos[0]
            data['position_y'] = arm_pos[1]
            data['position_z'] = arm_pos[2]

        if has['orientations']:
            orient = arrays['orientations'][arm_idx]
            data['orientation_w'] = orient[0]
            data['orientation_x'] = orient[1]
            data['orientation_y'] = orient[2]
            data['orientation_z'] = orient[3]

        if has['scopeframe_positions']:
            scope_pos = arrays['scopeframe_positions'][arm_idx]
            data['scope_position_x'] = scope_pos[0]
            data['scope_position_y'] = scope_pos[1]
            data['scope_position_z'] = scope_pos[2]

        if has['joint_angles']:
            for i, v in enumerate(arrays['joint_angles'][arm_idx], start=1):
                data[f'joint_{i}'] = v

        if has['gripper_angle']:
            data['gripper_angle'] = arrays['gripper_angle'][arm_idx]

        if has['gripper_signal']:
            data['gripper_signal'] = arrays['gripper_signal'][arm_idx]

        if has['energy_instrument_status']:
            data['energy_instrument_status'] = int(
                arrays['energy_instrument_status'][arm_idx]
            )

        metadata: dict[str, Any] = {
            'timestamp_mode': self.config.timestamp_mode,
            'ams_net_id': self.config.ams_net_id,
            'ams_port': self.config.ams_port,
            'ip_address': self.config.ip_address,
            'selected_arm': arm_id,
            'orientation_order': 'wxyz (to be confirmed)',
            'pc_cycle_start_ns': cycle_start_ns,
            'pc_arrival_ns': t_recv_ns,
            'rtt_ns': rtt_ns,
            'master_offset_ns': master_offset_ns,
            'conn_id': conn_id,
        }
        if self.config.use_rtt_correction:
            metadata['pc_arrival_corrected_ns'] = pc_arrival_ns
        if self.config.timestamp_mode == 'twincat' or self.config.output_dual_timestamps:
            metadata['twincat_cycle_count'] = int(arrays['__twincat_cycle_count'])
            metadata['twincat_cycle_time_ns'] = self._twincat_cycle_time_ns
        if self.config.output_dual_timestamps:
            metadata['timestamp_ns_pc'] = timestamp_ns_pc
            if timestamp_ns_plc is not None:
                metadata['timestamp_ns_plc'] = timestamp_ns_plc

        return DataSample(
            timestamp_ns=sample_ts_ns,
            source_name=self.name,
            data=data,
            metadata=metadata,
        )

    def _open_connection(self, conn_id: int) -> pyads.Connection:
        """Open a single ADS connection and log its id."""
        plc = pyads.Connection(
            self.config.ams_net_id,
            self.config.ams_port,
            self.config.ip_address,
        )
        plc.open()
        logger.info(
            'TwinCAT ADS connection %d/%d connected to %s:%d via %s',
            conn_id + 1,
            self.config.num_connections,
            self.config.ams_net_id,
            self.config.ams_port,
            self.config.ip_address,
        )
        return plc

    def _connection_worker(self, conn_id: int) -> None:
        """Acquisition loop for one parallel ADS connection."""
        period_s = self.config.cycle_period_ms / 1000.0
        plc: pyads.Connection | None = None
        local_errors = 0

        while not self._stop_event.is_set():
            if plc is None:
                try:
                    plc = self._open_connection(conn_id)
                    local_errors = 0
                except Exception as exc:
                    local_errors += 1
                    with self._lock:
                        self._consecutive_errors += 1
                    logger.error(
                        'Connection %d: failed to open ADS (attempt %d): %s',
                        conn_id,
                        local_errors,
                        exc,
                    )
                    time.sleep(min(period_s * 5, 1.0))
                    continue

            cycle_start_ns = time.time_ns()
            try:
                t_send_ns = time.time_ns()
                arrays = self._read_arrays(plc)
                t_recv_ns = time.time_ns()

                if self.config.output_all_arms:
                    # Output one sample per arm (1..4) from the same ADS read.
                    for arm_idx in range(4):
                        sample = self._build_sample(
                            cycle_start_ns, arrays, plc, t_send_ns, t_recv_ns,
                            conn_id, arm_idx=arm_idx
                        )
                        # Tag sample with per-arm source name so the writer can
                        # dispatch to separate CSV files.
                        sample.source_name = f'{self.name}_arm{arm_idx + 1}'
                        self._put_sample(sample)
                else:
                    sample = self._build_sample(
                        cycle_start_ns, arrays, plc, t_send_ns, t_recv_ns, conn_id
                    )
                    self._put_sample(sample)

                with self._lock:
                    self._samples_read += 1
                    self._consecutive_errors = 0
                local_errors = 0
            except Exception as exc:
                local_errors += 1
                with self._lock:
                    self._consecutive_errors += 1
                    self._missed_cycles += 1
                logger.error(
                    'Connection %d: ADS read error (consecutive %d): %s',
                    conn_id,
                    local_errors,
                    exc,
                )
                try:
                    plc.close()
                except Exception:
                    pass
                plc = None
                time.sleep(min(period_s * 5, 1.0))
                continue

            elapsed_s = (time.time_ns() - cycle_start_ns) / 1e9
            sleep_s = period_s - elapsed_s
            if sleep_s > 0:
                time.sleep(sleep_s)

        if plc is not None:
            try:
                plc.close()
            except Exception as exc:
                logger.warning(
                    'Connection %d: error closing ADS connection: %s',
                    conn_id,
                    exc,
                )

    def _acquire_loop(self) -> None:
        """Start and supervise parallel ADS connection workers."""
        num = max(1, self.config.num_connections)
        self._worker_threads = []
        self._connections = []

        for conn_id in range(num):
            t = threading.Thread(
                target=self._connection_worker,
                args=(conn_id,),
                name=f'{self.name}-ads-{conn_id}',
                daemon=True,
            )
            t.start()
            self._worker_threads.append(t)

        # Wait until stop is requested.
        for t in self._worker_threads:
            t.join()

    def get_writer_keys(self) -> list[str]:
        """Return the writer keys for this source.

        In multi-arm mode each arm gets its own writer key so samples can be
        routed to per-arm CSV files.
        """
        if self.config.output_all_arms:
            return [f'{self.name}_arm{i}' for i in range(1, 5)]
        return [self.name]

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
            'num_connections': self.config.num_connections,
            'output_all_arms': self.config.output_all_arms,
            'samples_read': self._samples_read,
            'missed_cycles': self._missed_cycles,
        })
        return summary


class _TwinCATCsvWriter:
    """Helper that writes a single TwinCAT CSV file."""

    def __init__(self, csv_path: Path):
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.csv_path, 'w', newline='')
        self._writer = None

    def write(self, row: dict[str, Any]) -> None:
        import csv
        if self._writer is None:
            self._writer = csv.DictWriter(self._f, fieldnames=list(row.keys()))
            self._writer.writeheader()
        self._writer.writerow(row)

    def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = None


class TwinCATWriter:
    """Writer for TwinCAT pose samples.

    Supports both single-arm mode (all samples written to the base directory)
    and multi-arm mode (samples dispatched to per-arm subdirectories based on
    ``sample.source_name``).
    """

    def __init__(self, source_name: str, out_dir: Path):
        self.source_name = source_name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._base_writer = _TwinCATCsvWriter(self.out_dir / 'twincat_poses.csv')
        self._arm_writers: dict[str, _TwinCATCsvWriter] = {}

    def _arm_writer(self, source_name: str) -> _TwinCATCsvWriter:
        """Return (creating if necessary) the CSV writer for a source name."""
        if source_name == self.source_name:
            return self._base_writer
        writer = self._arm_writers.get(source_name)
        if writer is None:
            writer = _TwinCATCsvWriter(self.out_dir / source_name / 'twincat_poses.csv')
            self._arm_writers[source_name] = writer
        return writer

    def write(self, sample: DataSample) -> None:
        data = sample.data
        row = {
            'timestamp_ns': sample.timestamp_ns,
            **data,
            **sample.metadata,
        }
        writer = self._arm_writer(sample.source_name)
        writer.write(row)

    def close(self) -> None:
        self._base_writer.close()
        for writer in self._arm_writers.values():
            writer.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
