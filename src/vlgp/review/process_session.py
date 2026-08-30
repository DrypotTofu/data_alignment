"""One-command post-acquisition pipeline driver.

Chains the standard per-session processing steps (README「采集后的数据处理流程」):

    (optional) write_session_meta -> verify_alignment -> align_camera_anchor
    -> review_session (GUI, interactive) -> build_srth_dataset -> images_to_video x3

Each step runs as a subprocess; a non-zero exit code aborts the pipeline.
After the review GUI closes, annotations.json is inspected: sessions marked
"rejected" stop here (nothing to build); anything else proceeds.

Usage:
    python -m vlgp.review.process_session --session sessions/20260818_215014
    python -m vlgp.review.process_session --session sessions/20260818_215014 --meta
    python -m vlgp.review.process_session --session sessions/20260818_215014 --skip-review
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Tool module -> package mapping for `python -m vlgp.<pkg>.<module>` invocation.
STEP_MODULES = {
    'write_session_meta': 'vlgp.review.write_session_meta',
    'verify_alignment': 'vlgp.alignment.verify_alignment',
    'align_camera_anchor': 'vlgp.alignment.align_camera_anchor',
    'review_session': 'vlgp.review.review_session',
    'build_srth_dataset': 'vlgp.dataset.build_srth_dataset',
    'images_to_video': 'vlgp.dataset.images_to_video',
}

CAMERA_DIRS = ('camera_arm_2', 'camera_arm_1', 'camera_arm_3')


def resolve_session(path: Path) -> Path:
    """Resolve a session dir from any working directory (same rules as review_session)."""
    candidates = [Path(path)]
    if not path.is_absolute():
        candidates.append(REPO_ROOT / path)
        candidates.append(Path.cwd() / path)
    for cand in candidates:
        if cand.is_dir():
            return cand.resolve()
    raise SystemExit(f'[ABORT] session directory not found: {path}')


def run_step(title: str, module: str, script_args: list[str]) -> None:
    """Run one vlgp tool module as a subprocess; abort the pipeline on failure."""
    cmd = [sys.executable, '-m', module, *script_args]
    print(f'\n===== {title} =====')
    print(' '.join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f'[ABORT] step failed with exit code {result.returncode}: {title}')


def review_outcome(session_dir: Path) -> str:
    """Return the annotation status after the review GUI closes."""
    ann_path = session_dir / 'annotations.json'
    if not ann_path.exists():
        return 'pending'
    try:
        return json.loads(ann_path.read_text(encoding='utf-8')).get('status', 'pending')
    except (json.JSONDecodeError, OSError):
        return 'pending'


def main() -> int:
    parser = argparse.ArgumentParser(description='Post-acquisition session pipeline')
    parser.add_argument('--session', type=Path, required=True,
                        help='Session directory (e.g. sessions/20260818_215014)')
    parser.add_argument('--meta', action='store_true',
                        help='Run write_session_meta.py first (interactive; do this '
                             'right after capture, task info cannot be recovered later)')
    parser.add_argument('--skip-review', action='store_true',
                        help='Skip the interactive review/trim GUI')
    parser.add_argument('--skip-video', action='store_true',
                        help='Skip preview video generation')
    args = parser.parse_args()

    session_dir = resolve_session(args.session)
    print(f'Session: {session_dir}')

    # Build the step list dynamically so numbering stays correct when
    # --meta / --skip-review / --skip-video change what actually runs.
    steps: list[tuple[str, str, list[str]]] = []
    if args.meta:
        steps.append(('session metadata (interactive)', STEP_MODULES['write_session_meta'],
                      ['--session', str(session_dir)]))
    steps.append(('alignment precheck', STEP_MODULES['verify_alignment'],
                  ['--session', str(session_dir)]))
    steps.append(('build 30 Hz alignment table', STEP_MODULES['align_camera_anchor'],
                  ['--session', str(session_dir)]))
    if not args.skip_review:
        steps.append(('human review / trim (GUI closes to continue)',
                      STEP_MODULES['review_session'], ['--session', str(session_dir)]))
    steps.append(('build SRT-H episode', STEP_MODULES['build_srth_dataset'],
                  ['--session', str(session_dir)]))
    if not args.skip_video:
        for cam in CAMERA_DIRS:
            arm = cam.rsplit('_', 1)[-1]
            steps.append((f'preview video ({cam})', STEP_MODULES['images_to_video'],
                          ['--dir', str(session_dir / cam),
                           '--output', str(session_dir / f'arm{arm}_preview.mp4'),
                           '--timestamp-overlay']))

    total = len(steps)
    for i, (label, module, cmd) in enumerate(steps, 1):
        run_step(f'{i}/{total} {label}', module, cmd)
        if label.startswith('human review'):
            status = review_outcome(session_dir)
            print(f'Review outcome: {status}')
            if status == 'rejected':
                print('[STOP] Session marked rejected; nothing to build. '
                      'annotations.json kept as the decision record.')
                return 0

    print('\n[DONE] Session pipeline finished.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
