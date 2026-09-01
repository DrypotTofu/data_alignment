"""Shared helpers used by both the web review tool and the legacy PyQt5 tool."""
from __future__ import annotations

import os


def os_user() -> str:
    """Return the current OS username (Windows USERNAME, Unix USER)."""
    return os.environ.get('USERNAME') or os.environ.get('USER') or ''
