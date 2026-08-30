"""Comprehensive session analysis: alignment, timing, sampling stability, trajectory.

Usage:
    python -m vlgp.analysis.analyze_session --session sessions/test_20260803_104724
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


from vlgp.acquisition.session_layout import DEFAULT_CAMERA_NAMES, camera_metadata_path, resolve_robot_pose_csv
from vlgp.acquisition.clock_fit import fit_linear_timestamps


def load_session_data(session_dir: Path) -> tuple[dict, pd.DataFrame, dict[str, pd.DataFrame]]:
    """Load session.json, robot_pose CSV, and camera metadata CSVs."""
    with open(session_dir / 'session.json', 'r', encoding='utf-8') as f:
        session_info = json.load(f)

    robot_path = resolve_robot_pose_csv(session_dir, arm_id=1)
    robot_df = pd.read_csv(robot_path) if robot_path is not None else None

    cameras = {}
    for arm in DEFAULT_CAMERA_NAMES:
        meta_path = camera_metadata_path(session_dir, arm)
        if meta_path.exists():
            # Multi-writer camera workers can interleave metadata rows.
            cameras[arm] = pd.read_csv(meta_path).sort_values('timestamp_ns').reset_index(drop=True)

    return session_info, robot_df, cameras


def analyze_twincat(robot_df: pd.DataFrame, out_dir: Path, make_plots: bool = True) -> dict:
    """Analyze TwinCAT sampling behavior."""
    print('\n=== TwinCAT 采样分析 ===')
    results = {}

    ts = robot_df['timestamp_ns'].values.astype(np.float64)
    dt_ms = np.diff(ts) / 1e6
    duration_s = (ts[-1] - ts[0]) / 1e9
    samples = len(robot_df)

    # Effective frequency
    freq_hz = samples / duration_s if duration_s > 0 else 0
    print(f'  样本数: {samples}')
    print(f'  持续时间: {duration_s:.3f} s')
    print(f'  实际平均频率: {freq_hz:.1f} Hz')
    print(f'  采样间隔: 均值={dt_ms.mean():.3f} ms, 标准差={dt_ms.std():.3f} ms, 中位数={np.median(dt_ms):.3f} ms')
    print(f'  采样间隔范围: [{dt_ms.min():.3f}, {dt_ms.max():.3f}] ms')

    results['samples'] = samples
    results['duration_s'] = duration_s
    results['freq_hz'] = freq_hz
    results['dt_mean_ms'] = dt_ms.mean()
    results['dt_std_ms'] = dt_ms.std()

    # RTT analysis
    if 'rtt_ns' in robot_df.columns:
        rtt_ms = robot_df['rtt_ns'].values / 1e6
        print(f'  ADS RTT: 均值={rtt_ms.mean():.3f} ms, 标准差={rtt_ms.std():.3f} ms, 中位数={np.median(rtt_ms):.3f} ms')
        print(f'  ADS RTT 范围: [{rtt_ms.min():.3f}, {rtt_ms.max():.3f}] ms')
        results['rtt_mean_ms'] = rtt_ms.mean()
        results['rtt_std_ms'] = rtt_ms.std()

        if make_plots:
            plt.figure(figsize=(10, 5))
            plt.hist(rtt_ms, bins=80, color='steelblue', edgecolor='white')
            plt.xlabel('ADS RTT (ms)')
            plt.ylabel('Count')
            plt.title('TwinCAT ADS Round-Trip Time Distribution')
            plt.axvline(rtt_ms.mean(), color='red', linestyle='--', label=f'mean={rtt_ms.mean():.2f} ms')
            plt.axvline(np.median(rtt_ms), color='green', linestyle='--', label=f'median={np.median(rtt_ms):.2f} ms')
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(out_dir / 'twincat_rtt_histogram.png', dpi=150)
            plt.close()

            plt.figure(figsize=(12, 5))
            t0 = ts[0]
            plt.plot((ts - t0) / 1e9, rtt_ms, linewidth=0.5, alpha=0.7)
            plt.xlabel('Time (s)')
            plt.ylabel('ADS RTT (ms)')
            plt.title('TwinCAT ADS RTT Over Time')
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(out_dir / 'twincat_rtt_timeseries.png', dpi=150)
            plt.close()

    # Sampling interval over time
    if make_plots:
        plt.figure(figsize=(12, 5))
        t0 = ts[0]
        plt.plot((ts[1:] - t0) / 1e9, dt_ms, linewidth=0.5, alpha=0.7)
        plt.xlabel('Time (s)')
        plt.ylabel('Sampling interval (ms)')
        plt.title('TwinCAT Sampling Interval Over Time')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_dir / 'twincat_sampling_interval.png', dpi=150)
        plt.close()

    # Clock drift: PLC master axis vs PC. Align/verify fit timestamp_ns
    # (canonical master axis, always present) against timestamp_ns_pc;
    # timestamp_ns_plc is dual-mode-only and would silently skip this
    # branch for pc_arrival sessions (CODE_REVIEW_20260830 2.3).
    if 'timestamp_ns_pc' in robot_df.columns and 'timestamp_ns' in robot_df.columns:
        pc = robot_df['timestamp_ns_pc'].values.astype(np.float64)
        plc = robot_df['timestamp_ns'].values.astype(np.float64)
        clock_fit = fit_linear_timestamps(pc, plc)
        k = clock_fit.slope
        b = clock_fit.intercept
        residuals = np.asarray(clock_fit.residuals, dtype=np.float64)

        print(f'  PLC-PC 时钟漂移模型: timestamp_ns = {k:.12f} * timestamp_ns_pc + {b:.0f}')
        print(f'  频率偏差: {(k - 1.0) * 1e6:+.3f} ppm')
        print(f'  残差标准差: {clock_fit.residual_std:.0f} ns')
        print(f'  残差最大绝对值: {clock_fit.residual_max_abs:.0f} ns')

        results['clock_drift_k'] = k
        results['clock_drift_b_ns'] = b
        results['clock_drift_ppm'] = (k - 1.0) * 1e6
        results['clock_residual_std_ns'] = clock_fit.residual_std
        results['clock_residual_max_ns'] = clock_fit.residual_max_abs

        if make_plots:
            plt.figure(figsize=(12, 5))
            plt.plot((pc - pc[0]) / 1e9, residuals / 1e6, linewidth=0.5, alpha=0.7)
            plt.xlabel('Time (s)')
            plt.ylabel('PLC-PC clock residual (ms)')
            plt.title('PLC vs PC Clock Drift Residual')
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(out_dir / 'twincat_clock_drift_residual.png', dpi=150)
            plt.close()

    return results


def analyze_cameras(
    cameras: dict[str, pd.DataFrame],
    out_dir: Path,
    make_plots: bool = True,
) -> dict:
    """Analyze camera frame rate and timing stability."""
    print('\n=== 相机帧率分析 ===')
    results = {}

    axes = None
    if make_plots:
        _, axes = plt.subplots(len(cameras), 1, figsize=(12, 3 * len(cameras)), sharex=True)
        if len(cameras) == 1:
            axes = [axes]

    for idx, (arm, df) in enumerate(cameras.items()):
        ts = df['timestamp_ns'].values.astype(np.float64)
        dt_ms = np.diff(ts) / 1e6
        duration_s = (ts[-1] - ts[0]) / 1e9
        fps = len(df) / duration_s if duration_s > 0 else 0

        print(f'\n  {arm}:')
        print(f'    帧数: {len(df)}')
        print(f'    持续时间: {duration_s:.3f} s')
        print(f'    实际平均 FPS: {fps:.2f}')
        print(f'    帧间隔: 均值={dt_ms.mean():.2f} ms, 标准差={dt_ms.std():.2f} ms, 中位数={np.median(dt_ms):.2f} ms')
        print(f'    帧间隔范围: [{dt_ms.min():.2f}, {dt_ms.max():.2f}] ms')

        results[arm] = {
            'frames': len(df),
            'duration_s': duration_s,
            'fps': fps,
            'dt_mean_ms': dt_ms.mean(),
            'dt_std_ms': dt_ms.std(),
            'dt_min_ms': dt_ms.min(),
            'dt_max_ms': dt_ms.max(),
        }

        if make_plots:
            ax = axes[idx]
            t0 = ts[0]
            ax.plot((ts[1:] - t0) / 1e9, dt_ms, linewidth=0.5, alpha=0.7)
            ax.set_ylabel('Frame interval (ms)')
            ax.set_title(f'{arm}: target ~{1000/dt_ms.mean():.1f} fps, actual {fps:.2f} fps')
            ax.grid(True, alpha=0.3)
    if make_plots:
        axes[-1].set_xlabel('Time (s)')
        plt.suptitle('Camera Frame Intervals Over Time', y=1.02)
        plt.tight_layout()
        plt.savefig(out_dir / 'camera_frame_intervals.png', dpi=150)
        plt.close()

    if make_plots:
        # Histogram of frame intervals
        plt.figure(figsize=(10, 6))
        for arm, df in cameras.items():
            ts = df['timestamp_ns'].values.astype(np.float64)
            dt_ms = np.diff(ts) / 1e6
            plt.hist(dt_ms, bins=60, alpha=0.6, label=arm, density=True)
        plt.xlabel('Frame interval (ms)')
        plt.ylabel('Density')
        plt.title('Camera Frame Interval Distribution')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_dir / 'camera_frame_interval_histogram.png', dpi=150)
        plt.close()

    return results


def analyze_cross_modal(
    robot_df: pd.DataFrame,
    cameras: dict[str, pd.DataFrame],
    out_dir: Path,
    make_plots: bool = True,
) -> dict:
    """Analyze cross-modal timing: start/stop overlap, latency.

    Uses the PC-axis timestamp for TwinCAT when available so that robot and
    camera timestamps are in the same domain.
    """
    print('\n=== 跨模态时间同步分析 ===')
    results = {}

    # Prefer PC-axis timestamp for cross-modal comparison.
    if 'timestamp_ns_pc' in robot_df.columns:
        robot_ts = robot_df['timestamp_ns_pc'].values.astype(np.float64)
        ts_label = 'timestamp_ns_pc'
    else:
        robot_ts = robot_df['timestamp_ns'].values.astype(np.float64)
        ts_label = 'timestamp_ns'

    robot_start = robot_ts.min()
    robot_stop = robot_ts.max()

    print(f'  TwinCAT 时间范围 ({ts_label}): {robot_start:.0f} ~ {robot_stop:.0f} ns')
    print(f'  TwinCAT 持续时间: {(robot_stop - robot_start) / 1e9:.3f} s')

    cam_ranges = {}
    for arm, df in cameras.items():
        cam_start = df['timestamp_ns'].min()
        cam_stop = df['timestamp_ns'].max()
        cam_ranges[arm] = (cam_start, cam_stop)
        print(f'  {arm}: {cam_start:.0f} ~ {cam_stop:.0f} ns, 持续 {(cam_stop - cam_start) / 1e9:.3f} s')

    # Compute overlap with robot (now in same PC time domain).
    for arm, (cam_start, cam_stop) in cam_ranges.items():
        overlap_start = max(robot_start, cam_start)
        overlap_stop = min(robot_stop, cam_stop)
        overlap_ns = max(0, overlap_stop - overlap_start)
        print(f'  {arm} 与 TwinCAT 重叠: {overlap_ns / 1e9:.3f} s')
        results[f'{arm}_overlap_s'] = overlap_ns / 1e9

    # Camera start delays relative to robot (PC time domain).
    for arm, (cam_start, cam_stop) in cam_ranges.items():
        delay_ms = (cam_start - robot_start) / 1e6
        print(f'  {arm} 相对 TwinCAT 启动延迟: {delay_ms:.1f} ms')
        results[f'{arm}_start_delay_ms'] = delay_ms

    if make_plots:
        # Gantt-like timeline
        plt.figure(figsize=(12, 4))
        labels = ['TwinCAT'] + list(cameras.keys())
        starts = [robot_start] + [cam_ranges[a][0] for a in cameras.keys()]
        stops = [robot_stop] + [cam_ranges[a][1] for a in cameras.keys()]
        t0 = min(starts)

        for i, (label, start, stop) in enumerate(zip(labels, starts, stops)):
            plt.barh(i, (stop - start) / 1e9, left=(start - t0) / 1e9, height=0.5, alpha=0.7)
            plt.text((start - t0) / 1e9 + 0.5, i, label, va='center', ha='left', color='black')

        plt.yticks([])
        plt.xlabel('Time (s)')
        plt.title('Data Source Active Time Ranges')
        plt.grid(True, alpha=0.3, axis='x')
        plt.tight_layout()
        plt.savefig(out_dir / 'cross_modal_timeline.png', dpi=150)
        plt.close()

    return results


def analyze_trajectory(robot_df: pd.DataFrame, out_dir: Path, make_plots: bool = True) -> dict:
    """Analyze robot trajectory smoothness."""
    print('\n=== 机器人轨迹分析 ===')
    results = {}

    pos_cols = ['position_x', 'position_y', 'position_z']
    if not all(c in robot_df.columns for c in pos_cols):
        print('  位置数据缺失，跳过轨迹分析')
        return results

    pos = robot_df[pos_cols].values.astype(np.float64)
    ts = robot_df['timestamp_ns'].values.astype(np.float64)
    dt_s = np.diff(ts) / 1e9

    # Displacement per sample
    displacements = np.sqrt(np.sum(np.diff(pos, axis=0) ** 2, axis=1))
    velocities = displacements / dt_s

    print(f'  总位移: {displacements.sum():.3f} mm')
    print(f'  平均速度: {velocities.mean():.3f} mm/s')
    print(f'  速度标准差: {velocities.std():.3f} mm/s')
    print(f'  最大速度: {velocities.max():.3f} mm/s')

    results['total_displacement_mm'] = displacements.sum()
    results['velocity_mean_mm_s'] = velocities.mean()
    results['velocity_std_mm_s'] = velocities.std()
    results['velocity_max_mm_s'] = velocities.max()

    if make_plots:
        # 3D trajectory
        fig = plt.figure(figsize=(10, 8))
        ax = fig.add_subplot(111, projection='3d')
        ax.plot(pos[:, 0], pos[:, 1], pos[:, 2], linewidth=0.8, alpha=0.7)
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')
        ax.set_title('End-Effector 3D Trajectory (ArmFrame)')
        plt.tight_layout()
        plt.savefig(out_dir / 'trajectory_3d.png', dpi=150)
        plt.close()

        # Velocity over time
        plt.figure(figsize=(12, 5))
        plt.plot((ts[1:] - ts[0]) / 1e9, velocities, linewidth=0.5, alpha=0.7)
        plt.xlabel('Time (s)')
        plt.ylabel('Speed (mm/s)')
        plt.title('End-Effector Speed Over Time')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_dir / 'trajectory_speed.png', dpi=150)
        plt.close()

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description='Comprehensive session analysis')
    parser.add_argument('--session', type=Path, required=True, help='Session directory')
    parser.add_argument('--out-dir', type=Path, default=None, help='Output directory')
    parser.add_argument('--skip-plots', action='store_true',
                        help='Compute metrics without generating matplotlib figures')
    args = parser.parse_args()

    session_dir = args.session
    out_dir = args.out_dir or session_dir / 'alignment_analysis'
    out_dir.mkdir(parents=True, exist_ok=True)

    session_info, robot_df, cameras = load_session_data(session_dir)

    if robot_df is None or robot_df.empty:
        print('No robot pose data found.')
        return 1

    results = {}
    make_plots = not args.skip_plots
    results['twincat'] = analyze_twincat(robot_df, out_dir, make_plots=make_plots)
    results['cameras'] = analyze_cameras(cameras, out_dir, make_plots=make_plots)
    results['cross_modal'] = analyze_cross_modal(
        robot_df, cameras, out_dir, make_plots=make_plots,
    )
    results['trajectory'] = analyze_trajectory(robot_df, out_dir, make_plots=make_plots)

    # Save summary JSON
    summary_path = out_dir / 'analysis_summary.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n分析摘要已保存: {summary_path}')

    if make_plots:
        print(f'\n所有图表已保存到: {out_dir}')
        for p in sorted(out_dir.glob('*.png')):
            print(f'  {p.name}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
