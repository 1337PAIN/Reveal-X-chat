"""Contact rows are buttons, wired in where the list is drawn.

The row builder is tested on its own in tests/js/contact-row.test.mjs. These
check that the page actually uses it and that the button keeps the row's look
and the 44px touch target.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'app' / 'static'


def test_the_list_is_drawn_with_the_button_builder():
    presence = (STATIC / 'js' / 'chat' / 'presence.js').read_text(encoding='utf-8')
    draw = presence[presence.index('export function updateUsersList('):]
    draw = draw[:draw.index('\n}\n')]
    # updateUsersList groups the list (chat-menu-ui.js); each row comes from
    # contactRow, which adds the pin/mute/lock marks to the builder's button.
    row = presence[presence.index('function contactRow('):]
    row = row[:row.index('\n}\n')]
    assert 'revealxContactRow.buildContactRow(' in row
    assert 'contactRow' in draw
    draw += row
    assert "li.addEventListener('click'" not in draw
    assert 'user.profile_image' not in draw


def test_the_builder_is_loaded_before_the_chat_and_precached():
    index = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
    assert index.index('js/contact-row.js') < index.index('js/chat/main.js')
    assert "'/static/js/contact-row.js'" in (STATIC / 'sw.js').read_text(encoding='utf-8')


def test_the_button_keeps_the_row_look_and_the_touch_target():
    css = (STATIC / 'css' / 'glass.css').read_text(encoding='utf-8')
    rule = re.search(r'\.contact-button \{([^}]*)\}', css).group(1)
    for decl in ('background: transparent', 'border: 0', 'width: 100%', 'min-height: 44px',
                 'font: inherit', 'color: inherit', 'text-align: left'):
        assert decl in rule, decl
