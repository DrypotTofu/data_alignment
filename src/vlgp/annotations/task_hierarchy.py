"""T15 controlled vocabulary (task hierarchy) loader + validator.

Consumes ``scripts/config/task_hierarchy.yaml`` (placeholder v0.1; status
field marks placeholder/draft/finalized) and exposes a :class:`TaskHierarchy`
object used by the annotation GUI (v1), the Web backend and the dataset build
integration:

- structural validation of the YAML (levels / tree / instruction templates);
- path membership checks for segment annotations
  (``annotations.json["segments"][i]["path"]``);
- instruction-template lookup per path (annotations must not be free text,
  PM decision, AGENT_HANDOFF T15).

Segment schema (AGENT_HANDOFF T15 #2, shared with the PyQt5 review tool):
``{"start_row": int, "end_row": int, "path": [task_id, subtask_id],
  "instruction": str, "corrective": bool}`` — row numbers are 0-based rows of
the camera-anchor alignment table; only `trim`/`status` old annotations stay
fully compatible.

CLI::

    python -m vlgp.annotations.task_hierarchy [--file <path>] [--segments <json>]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HIERARCHY_REL = Path('scripts/config/task_hierarchy.yaml')

PLACEHOLDER = 'placeholder'


@dataclass(frozen=True)
class TaskNode:
    """One node of the hierarchy (task or subtask)."""
    id: str
    label_zh: str
    label_en: str
    instructions: tuple[str, ...] = ()
    subtasks: tuple['TaskNode', ...] = ()


class TaskHierarchy:
    """Validated task-hierarchy vocabulary loaded from YAML."""

    def __init__(self, data: dict):
        errors = validate_structure(data)
        if errors:
            raise ValueError('task_hierarchy.yaml invalid:\n  ' + '\n  '.join(errors))
        self._data = data
        self.version = str(data.get('version', ''))
        self.status = str(data.get('status', PLACEHOLDER))
        self.levels: list[dict] = data.get('levels', [])
        self.tasks: tuple[TaskNode, ...] = tuple(
            _build_node(t) for t in data.get('hierarchy', []))

    # -- construction -----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> 'TaskHierarchy':
        """Load from ``path`` (default: repo ``scripts/config/task_hierarchy.yaml``)."""
        p = Path(path) if path is not None else REPO_ROOT / DEFAULT_HIERARCHY_REL
        data = yaml.safe_load(p.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError(f'{p}: expected a YAML mapping at top level')
        return cls(data)

    # -- queries ----------------------------------------------------------

    def is_placeholder(self) -> bool:
        return self.status == PLACEHOLDER

    def task_ids(self) -> list[str]:
        return [t.id for t in self.tasks]

    def all_paths(self) -> list[list[str]]:
        """Every valid (task, subtask) path, e.g. [['needle_passing', 'approach']]."""
        out: list[list[str]] = []
        for task in self.tasks:
            if task.subtasks:
                for sub in task.subtasks:
                    out.append([task.id, sub.id])
            else:
                out.append([task.id])
        return out

    def node_for_path(self, path) -> TaskNode | None:
        """Resolve a path (list[str] or 'task/subtask' string) to its leaf node."""
        parts = _normalise_path(path)
        if not parts:
            return None
        for task in self.tasks:
            if task.id != parts[0]:
                continue
            if len(parts) == 1:
                return task
            for sub in task.subtasks:
                if sub.id == parts[1]:
                    return sub
        return None

    def is_valid_path(self, path) -> bool:
        return self.node_for_path(path) is not None

    def instructions_for(self, path) -> tuple[str, ...]:
        node = self.node_for_path(path)
        return node.instructions if node else ()

    def label_for_path(self, path, sep: str = ' / ') -> str:
        """Human label (zh) for a path, e.g. '触针 / 夹持缝针'."""
        parts = _normalise_path(path)
        if not parts:
            return ''
        task = next((t for t in self.tasks if t.id == parts[0]), None)
        if task is None:
            return ''
        labels = [task.label_zh]
        if len(parts) > 1:
            sub = next((s for s in task.subtasks if s.id == parts[1]), None)
            if sub is not None:
                labels.append(sub.label_zh)
        return sep.join(labels)

    # -- segment validation (annotations.json["segments"]) -----------------

    def validate_segments(self, segments) -> list[str]:
        """Return human-readable problems for a segments list (empty = ok).

        Checks: shape, integer rows with end >= start, path membership,
        instruction from the node's template set (no free text), optional
        boolean ``corrective``. Does not check contiguity/coverage — that
        needs the trim range and is enforced by the editor.
        """
        problems: list[str] = []
        if not isinstance(segments, list):
            return ['segments: expected a list']
        for i, seg in enumerate(segments):
            tag = f'segments[{i}]'
            if not isinstance(seg, dict):
                problems.append(f'{tag}: expected an object')
                continue
            for key in ('start_row', 'end_row'):
                val = seg.get(key)
                if not isinstance(val, int):
                    problems.append(f'{tag}: {key} must be an int, got {val!r}')
            sr, er = seg.get('start_row'), seg.get('end_row')
            if isinstance(sr, int) and isinstance(er, int) and er < sr:
                problems.append(f'{tag}: end_row {er} < start_row {sr}')
            path = seg.get('path')
            if not self.is_valid_path(path):
                problems.append(f'{tag}: invalid path {path!r}')
            else:
                inst = seg.get('instruction')
                if not isinstance(inst, str) or not inst.strip():
                    problems.append(f'{tag}: instruction missing/empty')
                elif self.instructions_for(path) and inst not in self.instructions_for(path):
                    problems.append(
                        f'{tag}: instruction {inst!r} not in template set of {path!r}')
            if 'corrective' in seg and not isinstance(seg['corrective'], bool):
                problems.append(f'{tag}: corrective must be a bool')
        return problems


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _normalise_path(path) -> list[str] | None:
    if isinstance(path, str):
        parts = [p for p in path.split('/') if p]
    elif isinstance(path, (list, tuple)):
        parts = [p for p in path if isinstance(p, str)]
    else:
        return None
    return parts or None


def _build_node(d: dict) -> TaskNode:
    return TaskNode(
        id=str(d['id']),
        label_zh=str(d.get('label_zh', '')),
        label_en=str(d.get('label_en', '')),
        instructions=tuple(str(x) for x in d.get('instructions', [])),
        subtasks=tuple(_build_node(s) for s in d.get('subtasks', [])),
    )


def validate_structure(data: dict) -> list[str]:
    """Structural validation of the raw YAML mapping; returns error strings."""
    errors: list[str] = []
    levels = data.get('levels')
    if not isinstance(levels, list) or not levels:
        errors.append('levels: missing or empty')
    else:
        ids = [lv.get('id') for lv in levels if isinstance(lv, dict)]
        if len(ids) != len(set(ids)):
            errors.append('levels: duplicate ids')

    hier = data.get('hierarchy')
    if not isinstance(hier, list) or not hier:
        errors.append('hierarchy: missing or empty')
        return errors

    seen: set[str] = set()
    leaf_ok = True
    for task in hier:
        if not isinstance(task, dict) or 'id' not in task:
            errors.append('hierarchy: every task needs an "id"')
            continue
        tid = str(task['id'])
        if tid in seen:
            errors.append(f'hierarchy: duplicate task id {tid!r}')
        seen.add(tid)
        subs = task.get('subtasks', [])
        if not isinstance(subs, list):
            errors.append(f'{tid}: subtasks must be a list')
            continue
        sub_seen: set[str] = set()
        for sub in subs:
            if not isinstance(sub, dict) or 'id' not in sub:
                errors.append(f'{tid}: every subtask needs an "id"')
                continue
            sid = str(sub['id'])
            if sid in sub_seen:
                errors.append(f'{tid}: duplicate subtask id {sid!r}')
            sub_seen.add(sid)
            inst = sub.get('instructions', [])
            if not isinstance(inst, list) or not inst:
                errors.append(f'{tid}/{sid}: instructions missing or empty '
                              f'(leaf nodes must offer templates)')
                leaf_ok = False
            elif any(not isinstance(x, str) or not x.strip() for x in inst):
                errors.append(f'{tid}/{sid}: instructions must be non-empty strings')
                leaf_ok = False
        if not subs and not leaf_ok:
            pass  # task-level leaf without subtasks already reported via its own check
        if not subs:
            inst = task.get('instructions', [])
            if not isinstance(inst, list) or not inst:
                errors.append(f'{tid}: task without subtasks needs instructions')
    return errors


def self_check() -> dict[str, str]:
    """Load the repo vocabulary and validate a couple of segment examples."""
    hier = TaskHierarchy.load()
    results: dict[str, str] = {}
    results['yaml_loads'] = 'PASS'
    results['status'] = hier.status
    results['tasks'] = str(len(hier.tasks))
    results['all_paths'] = str(len(hier.all_paths()))
    first_path = hier.all_paths()[0]
    good = {'start_row': 10, 'end_row': 20,
            'path': first_path,
            'instruction': hier.instructions_for(first_path)[0],
            'corrective': False}
    bad = {'start_row': 30, 'end_row': 5,
           'path': first_path, 'instruction': '自由文本', 'corrective': 'yes'}
    results['segment_good'] = 'PASS' if not hier.validate_segments([good]) else 'FAIL'
    n = len(hier.validate_segments([bad]))
    results['segment_bad'] = f'PASS ({n} problems detected)' if n >= 3 else 'FAIL'
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description='T15 task hierarchy loader/validator')
    parser.add_argument('--file', type=Path, default=None,
                        help='Vocabulary YAML (default: repo scripts/config/task_hierarchy.yaml)')
    parser.add_argument('--segments', type=Path, default=None,
                        help='Optional annotations.json path to validate segments against')
    args = parser.parse_args()

    hier = TaskHierarchy.load(args.file)
    print(f'=== T15 task hierarchy: {args.file or DEFAULT_HIERARCHY_REL} ===')
    print(f'  version={hier.version} status={hier.status} '
          f'placeholder={hier.is_placeholder()}')
    print(f'  tasks={hier.task_ids()}')
    for p in hier.all_paths():
        print(f'    {p}  <- {hier.label_for_path(p)}')
        insts = hier.instructions_for(p)
        if insts:
            print(f'        instructions: {list(insts)}')

    if args.segments:
        ann = json.loads(args.segments.read_text(encoding='utf-8'))
        segs = ann.get('segments', [])
        problems = hier.validate_segments(segs)
        print(f'  segments validation: {len(segs)} segment(s), '
              f'{len(problems)} problem(s)')
        for p in problems:
            print(f'    - {p}')

    print()
    for k, v in self_check().items():
        print(f'  {k}: {v}')


if __name__ == '__main__':
    main()
