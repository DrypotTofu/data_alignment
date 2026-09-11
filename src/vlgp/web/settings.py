"""Persisted Web-platform settings (T15 extension).

The operator can point the web platform at any data folder (another disk,
an archive drive, an external SSD) instead of the fixed repo ``sessions/``
folder — the choice is persisted here so a restart reopens the same data
root, and the recent list makes switching back a single click.

Stored at ``var/web_settings.json`` (gitignored, repo-local, tiny):
    {"version": 1, "sessions_root": "D:\\\\vlgp_sessions", "recent_roots": [...]}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / 'sessions'
SETTINGS_PATH = REPO_ROOT / 'var' / 'web_settings.json'
MAX_RECENT = 8
VERSION = 1

# Data-root-side directories (cache/logs follow the chosen folder so a data
# disk that is nearly full does not also fill the repo disk).
PREVIEW_DIRNAME = '_web_preview'
LOG_DIRNAME = '_web_logs'


@dataclass
class WebSettings:
    sessions_root: str = str(DEFAULT_ROOT)
    recent_roots: list[str] = field(default_factory=list)
    path: Path = SETTINGS_PATH

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Path | None = None) -> 'WebSettings':
        """Load persisted settings; never raises (bad file -> defaults)."""
        p = Path(path) if path else SETTINGS_PATH
        s = cls(path=p)
        try:
            raw = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return s
        root = raw.get('sessions_root')
        if isinstance(root, str) and root.strip():
            s.sessions_root = root.strip()
        recent = raw.get('recent_roots')
        if isinstance(recent, list):
            s.recent_roots = [r for r in recent if isinstance(r, str) and r.strip()]
        return s

    def save(self) -> None:
        payload = {'version': VERSION,
                   'sessions_root': self.sessions_root,
                   'recent_roots': self.recent_roots}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding='utf-8')
        except OSError:
            # A read-only repo must not break browsing; the choice simply does
            # not survive a restart.
            pass

    # ----------------------------------------------------------------- write
    def remember(self, root: str | Path) -> None:
        """Remember ``root`` as current + most recent (dedup, most-recent-first)."""
        r = str(Path(root))
        self.sessions_root = r
        self.recent_roots = [r] + [x for x in self.recent_roots if x != r]
        del self.recent_roots[MAX_RECENT:]

    def existing_recent(self) -> list[str]:
        return [r for r in self.recent_roots if Path(r).is_dir()]
