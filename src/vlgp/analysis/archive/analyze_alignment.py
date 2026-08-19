"""Analyze cross-modal alignment error for an existing session.

Usage:
    python analyze_alignment.py --session sessions/test_20260803_104724
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


def analyze_session(session_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    arms = ['camera_arm_1', 'camera_arm_2', 'camera_arm_3']
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c']

    all_errors = {}
    all_data = {}

    for arm in arms:
        aligned_path = session_dir / arm / 'aligned_twincat.csv'
        if not aligned_path.exists():
            print(f'{arm}: aligned_twincat.csv not found, skipping')
            continue

        df = pd.read_csv(aligned_path)
        errors = df['alignment_error_ms'].values

        # Frames whose mapped PLC timestamp falls inside the TwinCAT range are
        # considered valid; outside samples hit the nearest endpoint and blow up.
        video_plc = df['video_timestamp_ns_plc'].values.astype(np.float64)
        twincat_plc = df['twincat_timestamp_ns_plc'].values.astype(np.float64)
        valid_mask = (video_plc >= twincat_plc.min()) & (video_plc <= twincat_plc.max())
        valid_errors = errors[valid_mask]

        all_errors[arm] = valid_errors
        all_data[arm] = df

        print(f'\n=== {arm} ===')
        print(f'  Total frames: {len(errors)}')
        print(f'  Valid frames: {len(valid_errors)} ({100 * len(valid_errors) / len(errors):.1f}%)')
        print(f'  Out-of-range frames: {len(errors) - len(valid_errors)}')
        if len(valid_errors) > 0:
            print(f'  Mean error: {valid_errors.mean():.3f} ms')
            print(f'  Std error:  {valid_errors.std():.3f} ms')
            print(f'  Max error:  {valid_errors.max():.3f} ms')
            print(f'  Median error: {np.median(valid_errors):.3f} ms')
            print(f'  95th percentile: {np.percentile(valid_errors, 95):.3f} ms')
            print(f'  99th percentile: {np.percentile(valid_errors, 99):.3f} ms')

    if not all_errors:
        print('No alignment data found.')
        return

    # 1. Histogram of alignment errors
    plt.figure(figsize=(10, 6))
    for arm, color in zip(all_errors.keys(), colors):
        plt.hist(all_errors[arm], bins=50, alpha=0.6, label=arm, color=color, density=True)
    plt.xlabel('Alignment error (ms)')
    plt.ylabel('Density')
    plt.title('Alignment Error Distribution')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_dir / 'alignment_error_histogram.png', dpi=150)
    plt.close()

    # 2. CDF of alignment errors
    plt.figure(figsize=(10, 6))
    for arm, color in zip(all_errors.keys(), colors):
        sorted_err = np.sort(all_errors[arm])
        cdf = np.arange(1, len(sorted_err) + 1) / len(sorted_err)
        plt.plot(sorted_err, cdf, label=arm, color=color, linewidth=2)
    plt.xlabel('Alignment error (ms)')
    plt.ylabel('CDF')
    plt.title('Alignment Error Cumulative Distribution')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.xlim(0, None)
    plt.tight_layout()
    plt.savefig(out_dir / 'alignment_error_cdf.png', dpi=150)
    plt.close()

    # 3. Time series of alignment errors
    plt.figure(figsize=(12, 6))
    for arm, color in zip(all_errors.keys(), colors):
        df = all_data[arm]
        t0 = df['video_timestamp_ns_pc'].iloc[0]
        t_s = (df['video_timestamp_ns_pc'].values - t0) / 1e9
        plt.plot(t_s, df['alignment_error_ms'].values, label=arm, color=color, alpha=0.7, linewidth=0.8)
    plt.xlabel('Time since first frame (s)')
    plt.ylabel('Alignment error (ms)')
    plt.title('Alignment Error Over Time')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_dir / 'alignment_error_timeseries.png', dpi=150)
    plt.close()

    # 4. Box plot comparison
    plt.figure(figsize=(8, 6))
    error_list = [all_errors[a] for a in all_errors.keys()]
    labels = list(all_errors.keys())
    bp = plt.boxplot(error_list, patch_artist=True)
    plt.xticks(np.arange(1, len(labels) + 1), labels)
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)
    plt.ylabel('Alignment error (ms)')
    plt.title('Alignment Error Comparison')
    plt.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(out_dir / 'alignment_error_boxplot.png', dpi=150)
    plt.close()

    # 5. Scatter: alignment error vs video timestamp (to check drift)
    fig, axes = plt.subplots(len(all_errors), 1, figsize=(12, 3 * len(all_errors)), sharex=True)
    if len(all_errors) == 1:
        axes = [axes]
    for ax, (arm, color) in zip(axes, zip(all_errors.keys(), colors)):
        df = all_data[arm]
        t0 = df['video_timestamp_ns_pc'].iloc[0]
        t_s = (df['video_timestamp_ns_pc'].values - t0) / 1e9
        ax.scatter(t_s, df['alignment_error_ms'].values, s=3, alpha=0.5, color=color)
        ax.set_ylabel('Error (ms)')
        ax.set_title(arm)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel('Time since first frame (s)')
    plt.suptitle('Alignment Error vs Time (per camera)', y=1.02)
    plt.tight_layout()
    plt.savefig(out_dir / 'alignment_error_scatter.png', dpi=150)
    plt.close()

    print(f'\nPlots saved to: {out_dir}')
    for p in sorted(out_dir.glob('*.png')):
        print(f'  {p.name}')


def main() -> int:
    parser = argparse.ArgumentParser(description='Analyze alignment error for a session')
    parser.add_argument('--session', type=Path, required=True, help='Session directory')
    parser.add_argument('--out-dir', type=Path, default=None, help='Output directory for plots')
    args = parser.parse_args()

    out_dir = args.out_dir or args.session / 'alignment_analysis'
    analyze_session(args.session, out_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
