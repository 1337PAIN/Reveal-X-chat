"""Message actions: big enough to hit, and delete asks in the app.

.btn-react was 13px text with 2px padding, about 21 x 17px; delete was a bare
16px "x" with no padding at all, beside the timestamp. On a phone both were
easy to miss and the delete easy to hit by accident -- and it then asked
through window.confirm(). WCAG 2.5.8 asks for 24 x 24px; on touch screens
the app's own rule is 44 x 44px, from padding rather than a bigger glyph.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'app' / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
RENDER = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'render.js').read_text(encoding='utf-8')
MESSAGING = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'messaging.js').read_text(encoding='utf-8')
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')


def rule(css, selector):
    match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    assert match, selector
    return match.group(1)


def test_message_actions_are_at_least_24px_everywhere():
    body = rule(CSS, '.btn-react,\n.btn-message-menu')
    assert 'min-width: 24px' in body and 'min-height: 24px' in body
    assert 'font-size' not in body, 'size comes from padding, not a bigger glyph'


def test_and_44px_on_touch_screens():
    block = CSS[CSS.index('@media (pointer: coarse)'):]
    block = block[:block.index('\n}\n')]
    assert '.btn-react' in block and '.btn-message-menu' in block
    assert 'min-width: 44px' in block and 'min-height: 44px' in block


def test_delete_lives_in_a_per_message_menu():
    assert 'btn-delete-message' not in RENDER
    menus = re.findall(r'<button class="btn-message-menu"[^>]*>', RENDER)
    assert len(menus) == 3, 'text, voice and share messages'
    for button in menus:
        assert 'aria-haspopup="menu"' in button and 'aria-expanded="false"' in button
        assert 'aria-label=' in button
    assert 'revealxDialogs.wireMenu(' in RENDER


def test_delete_asks_in_the_app_not_with_confirm():
    body = MESSAGING[MESSAGING.index('function deleteMessage('):]
    body = body[:body.index('\n}\n')]
    assert 'confirm(' not in body.replace('askConfirm(', '')
    assert 'revealxDialogs.askConfirm(' in body
    for element_id in ('confirmDialog', 'confirmDialogTitle', 'confirmDialogBody',
                       'confirmDialogOk', 'confirmDialogCancel'):
        assert f'id="{element_id}"' in INDEX
    assert INDEX.index('js/dialogs.js') < INDEX.index('js/chat/main.js')
