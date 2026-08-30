"""Analyze ``gripper_angle`` range/distribution across recorded sessions.

T1 deliverable (AGENT_HANDOFF.md): offline reverse-engineering of the gripper
angle scale and direction from existing TwinCAT CSVs.

What this tool reports per arm:
  - sample count, min / max / mean / std,
  - percentiles p1/p5/p25/p50/p75/p95/p99 (robust working range),
  - ``gripper_signal`` stats (used to infer open/close direction when it
    carries a live signal; currently the PLC emits a constant value),
  - per-session table so arms that actually moved their gripper stand out.

Direction conclusion is printed separately: with a constant ``gripper_signal``
and no commanded open/close ground truth in any session, the open/close sign
cannot be proven offline; a 1-minute live test (open -> close -> open on one
arm while recording) settles it. The tool therefore reports the empirical
range and a provisional convention (larger angle = more open, dVRK-style)
which ``build_srth_dataset.py`` makes invertible via its ``jaw_invert`` flag.

Usage:
    python -m vlgp.analysis.analyze_gripper [--sessions-dir sessions]
                                            [--out var/gripper_analysis.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


from vlgp.acquisition.session_layout import find_per_arm_robot_pose_csvs, legacy_robot_pose_path

ANGLE_COL = 'gripper_angle'
SIGNAL_COL = 'gripper_signal'
PERCENTILES = (1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0)


def _stats(s: pd.Series) -> dict | None:
    s = s.dropna()
    if len(s) == 0:
        return None
    return {
        'n': int(len(s)),
        'min': float(s.min()),
        'max': float(s.max()),
        'mean': float(s.mean()),
        'std': float(s.std()),
        'p1': float(s.quantile(0.01)),
        'p5': float(s.quantile(0.05)),
        'p25': float(s.quantile(0.25)),
        'p50': float(s.quantile(0.50)),
        'p75': float(s.quantile(0.75)),
        'p95': float(s.quantile(0.95)),
        'p99': float(s.quantile(0.99)),
    }


def analyze_session(session_dir: Path) -> dict:
    """Per-arm gripper stats for one session."""
    result: dict[str, dict] = {}
    arms = find_per_arm_robot_pose_csvs(session_dir)
    if not arms:
        legacy = legacy_robot_pose_path(session_dir)
        if legacy is not None:
            df = pd.read_csv(legacy)
            if 'arm' in df.columns:
                arms = {int(arm_id): legacy for arm_id in sorted(df['arm'].unique())}
            else:
                arms = {1: legacy}
    for arm_id in sorted(arms):
        path = arms[arm_id]
        df = pd.read_csv(path)
        if 'arm' in df.columns:
            df = df[df['arm'] == arm_id]
        entry: dict = {}
        if ANGLE_COL in df.columns:
            entry['gripper_angle'] = _stats(df[ANGLE_COL])
        if SIGNAL_COL in df.columns:
            sig = df[SIGNAL_COL].dropna()
            entry['gripper_signal'] = {
                'n': int(len(sig)),
                'min': float(sig.min()) if len(sig) else None,
                'max': float(sig.max()) if len(sig) else None,
                'mean': float(sig.mean()) if len(sig) else None,
                'nonzero_frac': float((sig != 0).mean()) if len(sig) else None,
            }
        result[f'arm{arm_id}'] = entry
    return result


def aggregate(sessions: dict[str, dict]) -> dict:
    """Merge per-arm stats across sessions into one table."""
    merged: dict[str, list] = {f'arm{a}': [] for a in range(1, 5)}
    for sname, arms in sessions.items():
        for arm_key, entry in arms.items():
            ga = entry.get('gripper_angle')
            if ga is not None:
                merged[arm_key].append({'session': sname, **ga})
    agg: dict[str, dict] = {}
    for arm_key, rows in merged.items():
        if not rows:
            agg[arm_key] = {'sessions_with_data': 0, 'n_total': 0}
            continue
        df = pd.DataFrame(rows)
        agg[arm_key] = {
            'sessions_with_data': len(df),
            'n_total': int(df['n'].sum()),
            'global_min': float(df['min'].min()),
            'global_max': float(df['max'].max()),
            'sessions': rows,
        }
    return agg


def direction_conclusion(sessions: dict[str, dict]) -> dict:
    """Best-effort open/close direction inference from the recorded data."""
    signals = []
    moving_ranges = []
    for sname, arms in sessions.items():
        for arm_key, entry in arms.items():
            sig = entry.get('gripper_signal')
            if sig and sig.get('n'):
                signals.append((sname, arm_key, sig))
            ga = entry.get('gripper_angle')
            if ga and (ga['max'] - ga['min']) > 2.0:
                moving_ranges.append((sname, arm_key, ga['min'], ga['max']))
    all_zero = all(s[2]['nonzero_frac'] == 0 for s in signals) if signals else None
    return {
        'signal_present': bool(signals),
        'signal_all_zero': all_zero,
        'arms_with_motion': [
            {'session': s, 'arm': a, 'min': lo, 'max': hi}
            for s, a, lo, hi in moving_ranges
        ],
        'conclusion': (
            'direction undetermined offline: gripper_signal is constant (0) in every session '
            'and no session contains a commanded open/close with known ground truth; '
            'provisional convention is larger angle = more open (dVRK-style), final sign '
            'must be confirmed by a live open/close test (see AGENT_HANDOFF T5/T6)'
            if all_zero
            else 'gripper_signal carries variation; inspect it for direction'
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions-dir', type=Path, default=Path('sessions'))
    parser.add_argument('--out', type=Path, default=None,
                        help='Write JSON summary to this path')
    args = parser.parse_args()

    sessions: dict[str, dict] = {}
    for sdir in sorted(args.sessions_dir.iterdir()):
        if not sdir.is_dir():
            continue
        try:
            result = analyze_session(sdir)
        except Exception as exc:
            print(f'{sdir.name}: skipped ({exc})')
            continue
        if any(v for v in result.values()):
            sessions[sdir.name] = result

    agg = aggregate(sessions)
    conclusion = direction_conclusion(sessions)

    # Compact per-arm summary across all data.
    print('=== gripper_angle global stats per arm (all sessions) ===')
    for arm_key, info in agg.items():
        if info.get('sessions_with_data', 0) == 0:
            print(f'{arm_key}: no data')
            continue
        print(f"{arm_key}: sessions={info['sessions_with_data']} n={info['n_total']} "
              f"global min={info['global_min']:.3f} max={info['global_max']:.3f}")

    print('\n=== per-session working range (arms with span > 2.0) ===')
    for sname, arms in sessions.items():
        for arm_key, entry in arms.items():
            ga = entry.get('gripper_angle')
            if ga and (ga['max'] - ga['min']) > 2.0:
                print(f"  {sname} {arm_key}: min={ga['min']:.3f} max={ga['max']:.3f} "
                      f"p5={ga['p5']:.3f} p95={ga['p95']:.3f}")

    print('\n=== direction conclusion ===')
    print(json.dumps(conclusion, ensure_ascii=False, indent=2))

    out = {
        'generated_by': 'vlgp.analysis.analyze_gripper (T1)',
        'per_arm_global': agg,
        'direction': conclusion,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nJSON summary written to {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
