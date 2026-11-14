"""Every message shows one status chip, built from chips.js, never in red.

The rules are tested in tests/js/chips.test.mjs. These check the bubbles use
them, that the old badges are gone, and that no chip style uses the danger
colour.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'app' / 'static'
RENDER = (STATIC / 'js' / 'chat' / 'render.js').read_text(encoding='utf-8')
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
CSS = '\n'.join((STATIC / 'css' / name).read_text(encoding='utf-8')
                for name in ('style.css', 'glass.css', 'settings.css'))


def test_each_kind_of_message_draws_a_chip():
    assert RENDER.count('<span class="msg-chip"></span>') == 3, 'text, voice and share'
    assert 'revealxChips.messageChip(' in RENDER
    assert INDEX.index('js/chips.js') < INDEX.index('js/chat/main.js')


def test_the_old_badges_are_gone():
    for old in ('ONE-TIME ACCESS SHARE', 'class="legacy-badge"', 'class="e2e-badge"',
                'class="one-time-notice"', 'class="share-expiry"'):
        assert old not in RENDER, old


def test_no_chip_style_is_red():
    for body in re.findall(r'\.msg-chip[^{]*\{([^}]*)\}', CSS):
        assert '--danger' not in body and '#dc2626' not in body and '#991b1b' not in body
