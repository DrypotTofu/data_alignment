"""Write session-level metadata (T8) into an existing session directory.

The operator must record task information AT collection time — it cannot be
reconstructed after the fact. This tool writes ``<session>/session_meta.json``
from either an interactive prompt or a JSON file, without touching the
acquisition framework core.

Optionally merges the fields into ``session.json`` (off by default).

Usage:
    python -m vlgp.review.write_session_meta --session sessions/20260817_203000
    python -m vlgp.review.write_session_meta --session sessions/20260817_203000 \
        --json session_meta_input.json
    python -m vlgp.review.write_session_meta --session ... --merge-session-json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TASK_TYPES = [
    'needle_touching', 'needle_grasping', 'autonomous_suturing',
    'endoscope_adjust', 'calibration', 'other',
]

DEFAULT_TEMPLATE = {
    'session_name': '',
    'task_instruction': '',        # English instruction, SRT-H style
    'task_type': 'other',
    'subtask_sequence': [],        # optional list of subtasks
    'operator': '',
    'organ_model': '',             # organ/model batch id
    'instruments': {},             # e.g. {"arm_1": "grasper", "arm_3": "needle_driver"}
    'notes': '',
    'camera_calibration': '',      # T7: calibration id, e.g. calib_20260817_203000
}


def _ask(prompt: str, default: str = '') -> str:
    suffix = f' [{default}]' if default else ''
    try:
        value = input(f'{prompt}{suffix}: ').strip()
    except EOFError:
        value = ''
    return value or default


def interactive_prompt(meta: dict) -> dict:
    print('\n=== Session metadata (fill at collection time, cannot be backfilled) ===')
    meta['session_name'] = _ask('session name', meta.get('session_name', ''))
    meta['task_instruction'] = _ask('task instruction (EN)', meta.get('task_instruction', ''))
    print('task types: ' + ', '.join(TASK_TYPES))
    meta['task_type'] = _ask('task type', meta.get('task_type', 'other'))
    meta['operator'] = _ask('operator', meta.get('operator', ''))
    meta['organ_model'] = _ask('organ/model id', meta.get('organ_model', ''))
    meta['camera_calibration'] = _ask('calibration id (T7)', meta.get('camera_calibration', ''))
    # T14: instrument entry is optional here — when known at capture time it can
    # be pre-filled, but episode building always takes the TwinCAT-measured
    # instrument_type as the source of truth (see build_srth_dataset).
    instr = meta.get('instruments') or {}
    instr_hint = ','.join(f'{k}={v}' for k, v in instr.items()) if instr else ''
    instr_str = _ask('instruments (optional, e.g. arm_1=圆头抓取钳,arm_3=持针钳; '
                     'measured value wins at build time)', instr_hint)
    if instr_str:
        parsed = {}
        for pair in instr_str.split(','):
            if '=' in pair:
                k, v = pair.split('=', 1)
                parsed[k.strip()] = v.strip()
        meta['instruments'] = parsed
    meta['notes'] = _ask('notes', meta.get('notes', ''))
    return meta


def load_json_input(path: Path, template: dict) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    meta = dict(template)
    # Only copy fields that exist in the template; extra input keys are dropped
    # so session_meta.json always matches the known schema.
    meta.update({k: v for k, v in data.items() if k in template})
    return meta


def merge_into_session_json(session_dir: Path, meta: dict) -> None:
    path = session_dir / 'session.json'
    if not path.exists():
        print('  WARNING: session.json not found, skip merge')
        return
    data = json.loads(path.read_text(encoding='utf-8'))
    data.setdefault('session_meta', {}).update(meta)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'  merged into {path}')


def main() -> int:
    parser = argparse.ArgumentParser(description='Write session-level metadata (T8)')
    parser.add_argument('--session', type=Path, required=True,
                        help='Session directory, e.g. sessions/20260817_203000')
    parser.add_argument('--json', type=Path, default=None,
                        help='Read metadata from a JSON file instead of interactive prompt')
    parser.add_argument('--merge-session-json', action='store_true',
                        help='Also merge fields into session.json (default: off)')
    args = parser.parse_args()

    session_dir = args.session
    if not session_dir.exists():
        print(f'ERROR: session dir not found: {session_dir}')
        return 1

    template = dict(DEFAULT_TEMPLATE)
    meta = load_json_input(args.json, template) if args.json else interactive_prompt(template)

    if not meta.get('task_instruction'):
        print('WARNING: task_instruction is empty — fill it at collection time!')

    out = session_dir / 'session_meta.json'
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Written: {out}')
    if args.merge_session_json:
        merge_into_session_json(session_dir, meta)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
