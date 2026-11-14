"""No alert(), confirm() or prompt() anywhere in the shipped client.

They block the whole page, cannot be themed, ignore the app's language, and
some screen readers announce them as browser alerts unrelated to the app.
Questions go through dialogs.js askConfirm(), must-read messages through
showNotice(), and everything else through the toast live region.
"""

import re
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js'
SCRIPTS = sorted(p for p in JS.rglob('*.js')
                 if 'vendor' not in p.parts and p.name != 'a11y-audit.js')


def code_only(source):
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'(?m)^\s*//.*$', '', source)


@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: str(p.relative_to(JS)))
def test_no_native_dialogs(script):
    code = code_only(script.read_text(encoding='utf-8'))
    found = re.findall(r'(?<![\w.])(?:window\.)?(alert|confirm|prompt)\s*\(', code)
    assert not found, f'{script.name} still calls {sorted(set(found))}'


def test_the_toast_is_styled_and_respects_reduced_motion():
    css = (JS.parent / 'css' / 'settings.css').read_text(encoding='utf-8')
    assert '.app-toast {' in css and '.app-toast.error {' in css and '.app-toast.visible {' in css
    assert re.search(r'@media \(prefers-reduced-motion: reduce\) \{\s*\.app-toast[ ,{]', css), \
        'the toast slides in, so it must stop sliding under reduced motion'
