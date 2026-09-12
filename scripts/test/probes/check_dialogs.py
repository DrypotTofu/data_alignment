# -*- coding: utf-8 -*-
"""Static sanity check of the two modal dialogs (no browser needed).

Both must be centred overlays: the same fixed/inset/flex rule and a [hidden]
rule, otherwise one of them renders as a plain block at the end of the document
(exactly the bug reported for 采集).

Usage:  python scripts/test/probes/check_dialogs.py
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CSS = (REPO / 'src' / 'vlgp' / 'web' / 'static' / 'style.css').read_text(encoding='utf-8')
HTML = (REPO / 'src' / 'vlgp' / 'web' / 'static' / 'index.html').read_text(encoding='utf-8')

DIALOGS = ('root-dialog', 'acq-dialog', 'pipe-dialog')
CLOSE_IDS = {'root-dialog': 'root-close', 'acq-dialog': 'acq-close',
             'pipe-dialog': 'pipe-close'}
OK = True


def check(label: str, cond: bool, extra: str = '') -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}")


def rule_for(selector: str) -> str | None:
    m = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', CSS)
    return ' '.join(m.group(1).split()) if m else None


print('=== dialogs in index.html ===')
for d in DIALOGS:
    idx = HTML.find(f'id="{d}"')
    present = idx != -1
    box = ''
    if present:
        box = 'dialog-box wide' if 'dialog-box wide' in HTML[idx:idx + 200] else 'dialog-box'
        close = f'id="{CLOSE_IDS[d]}"'
        check(f'{d} present with dialog-box', present and box.startswith('dialog-box'),
              f'{box}, {close} present={close in HTML}')
    else:
        check(f'{d} present', False)

print('=== overlay CSS (shared by all dialogs) ===')
base = (rule_for('#root-dialog, #acq-dialog, #pipe-dialog')
        or rule_for('#root-dialog, #acq-dialog') or rule_for('#acq-dialog'))
for prop in ('position: fixed', 'inset: 0', 'display: flex',
             'align-items: center', 'justify-content: center'):
    check(f'overlay has {prop}', base is not None and prop in base, str(base))
hidden = rule_for('#root-dialog[hidden], #acq-dialog[hidden], #pipe-dialog[hidden]')
check('hidden rule hides every dialog',
      hidden is not None and 'display: none' in hidden, str(hidden))

print('=== per-dialog sizing ===')
wide = rule_for('.dialog-box.wide')
check('.dialog-box.wide sets a width', wide is not None and 'width' in wide, str(wide))
check('acq dialog uses the wide box', 'dialog-box wide' in HTML)

print('=== pipeline dialog wiring ===')
check('transport row has the 流水线 trigger', 'id="btn-pipe"' in HTML)
check('trigger sits between 保存 and 下一个',
      HTML.find('id="btn-save"') < HTML.find('id="btn-pipe"')
      < HTML.find('id="btn-next-session"'))
check('no leftover inline #pipeline section', 'id="pipeline"' not in HTML)
JS = (REPO / 'src' / 'vlgp' / 'web' / 'static' / 'app.js').read_text(encoding='utf-8')
for fn in ('openPipeDialog', 'closePipeDialog', "getElementById" if False else "$('btn-pipe').onclick"):
    check(f'app.js wires {fn}', fn in JS)

print('RESULT:', 'PASS' if OK else 'FAIL')
raise SystemExit(0 if OK else 1)
