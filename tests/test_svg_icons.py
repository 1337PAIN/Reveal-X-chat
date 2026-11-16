"""Controls use the SVG icon sprite, not emoji.

The browser logic is tested in tests/js/icons.test.mjs. This checks the
sprite is on the page with every symbol, that each control's markup points
into it with no emoji left, and that the script-built and script-switched
icons (keys, theme, voice, react, more) go through icons.js.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
JS = ROOT / 'app' / 'static' / 'js'
CSS = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'app' / 'static' / 'css').glob('*.css'))
SW = (ROOT / 'app' / 'static' / 'sw.js').read_text(encoding='utf-8')
ICONS = (JS / 'icons.js').read_text(encoding='utf-8')
EMOJI = re.compile(r'[←-⯿☀-➿\U0001F300-\U0001FAFF]')

NAMES = ['settings', 'rebuild', 'moon', 'sun', 'camera', 'mic', 'stop', 'close',
         'key', 'react', 'more', 'lock',
         'play', 'pause', 'send', 'trash', 'shield-check', 'shield-alert', 'user-plus', 'user-minus']


def sprite():
    start = INDEX.index('id="iconSprite"')
    return INDEX[start:INDEX.index('</svg>\n', start)]


@pytest.mark.parametrize('name', NAMES)
def test_the_sprite_has_every_symbol(name):
    assert f'<symbol id="i-{name}"' in sprite()
    assert f"'{name}'" in ICONS, f'icons.js does not know {name}'


def test_the_sprite_is_hidden_from_everyone():
    tag = re.search(r'<svg[^>]*id="iconSprite"[^>]*>', INDEX).group(0)
    assert 'aria-hidden="true"' in tag and 'class="icon-sprite"' in tag
    assert re.search(r'\.icon-sprite\s*\{[^}]*width:\s*0', CSS)


@pytest.mark.parametrize('element_id,name', [
    ('settingsBtn', 'settings'),
    ('reconstructImageBtn', 'rebuild'),
    ('themeToggle', 'moon'),
    ('shareImageBtn', 'camera'),
    ('voiceBtn', 'mic'),
    ('voiceCancelBtn', 'trash'),   # discarding a recording, not closing a dialog
])
def test_controls_point_into_the_sprite(element_id, name):
    body = re.search(rf'<button[^>]*\bid="{element_id}"[^>]*>(.*?)</button>', INDEX, re.S).group(1)
    assert f'<use href="#i-{name}"' in body
    assert 'aria-hidden="true"' in body
    assert not EMOJI.search(body), f'{element_id} still has an emoji'


def test_the_lock_screen_icon():
    card = INDEX[INDEX.index('class="lock-icon"'):]
    card = card[:card.index('</div>')]
    assert '<use href="#i-lock"' in card and not EMOJI.search(card)


def test_icons_follow_the_text_colour():
    rule = re.search(r'\n\.icon\s*\{([^}]*)\}', CSS).group(1)
    assert 'stroke: currentColor' in rule and 'fill: none' in rule


def test_script_built_icons_use_the_helper():
    shell = (JS / 'chat' / 'shell.js').read_text(encoding='utf-8')
    settings = (JS / 'chat' / 'settings.js').read_text(encoding='utf-8')
    messaging = (JS / 'chat' / 'messaging.js').read_text(encoding='utf-8')
    render = (JS / 'chat' / 'render.js').read_text(encoding='utf-8')
    assert "revealxIcons.icon(document, 'key')" in shell
    assert "revealxIcons.setIcon(" in settings and "'sun'" in settings
    assert "revealxIcons.icon(document, recording ? 'send' : 'mic')" in messaging
    for glyph in ('☺', '⋯', '\U0001F511', '\U0001F319', '☀', '⏹', '\U0001F3A4'):
        for source in (shell, settings, messaging, render):
            assert glyph not in source
    assert render.count('<use href="#i-react"></use>') == 3
    assert render.count('<use href="#i-more"></use>') == 3


def test_the_helper_loads_first_and_is_precached():
    assert INDEX.index('js/icons.js') < INDEX.index('js/chat/main.js')
    assert "'/static/js/icons.js'" in SW


@pytest.mark.parametrize('element_id', [
    'cancelReplyBtn', 'closePanel', 'closeKeyManagerBtn', 'closeSettingsBtn',
    'closeAdminBtn', 'totpSetupCloseBtn',
])
def test_close_buttons_draw_the_close_icon(element_id):
    # They were a "×" character (one an &times; entity), drawn in each
    # dialog's own font size and read as "times" wherever a label slipped.
    tag = re.search(rf'<button[^>]*\bid="{element_id}"[^>]*>(.*?)</button>', INDEX, re.S)
    assert tag, element_id
    assert 'aria-label="' in tag.group(0)
    body = tag.group(1)
    assert '<use href="#i-close"' in body
    assert '×' not in body and '&times;' not in body


def test_every_dialog_close_button_is_styled_as_one():
    # The accounts dialog's used class="close-btn", which no stylesheet
    # defines: an unstyled browser button, black on the dark dialog.
    assert 'class="close-btn"' not in INDEX
    tag = re.search(r'<button[^>]*\bid="closeAdminBtn"[^>]*>', INDEX).group(0)
    assert 'class="btn-close"' in tag
