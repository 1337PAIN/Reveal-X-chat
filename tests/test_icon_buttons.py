"""Icon-only buttons have names, and visible words where there is room.

A button's accessible name comes from its content before its title, so
"⚙️" was announced as "gear" (or nothing, depending on the reader) rather
than "Settings"; the reconstruct, theme and keys buttons were the same. The
sidebar footer has room for a word under each icon, so it gets one.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
SHELL = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'shell.js').read_text(encoding='utf-8')
SETTINGS = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'settings.js').read_text(encoding='utf-8')


def button(element_id):
    match = re.search(rf'<button[^>]*\bid="{element_id}"[^>]*>(.*?)</button>', INDEX, re.S)
    assert match, element_id
    return match.group(0), match.group(1)


@pytest.mark.parametrize('element_id,label', [
    ('settingsBtn', 'Settings'),
    ('reconstructImageBtn', 'Rebuild an image from two shares'),
    ('themeToggle', 'Switch to light theme'),
    ('shareImageBtn', 'Share an image'),
    ('voiceBtn', 'Record a voice message'),
])
def test_icon_buttons_are_named(element_id, label):
    tag, body = button(element_id)
    assert f'aria-label="{label}"' in tag
    # The glyph itself is decoration once the button has a name.
    assert 'aria-hidden="true"' in body, f'{element_id}: the icon is still read out'


@pytest.mark.parametrize('element_id,word', [('reconstructImageBtn', 'Rebuild'), ('themeToggle', 'Theme')])
def test_footer_buttons_show_a_word(element_id, word):
    _, body = button(element_id)
    assert f'<span class="btn-label">{word}</span>' in body


def test_the_keys_button_built_in_script_is_named_and_labelled():
    block = SHELL[SHELL.index("const keysBtn = document.createElement('button');"):]
    block = block[:block.index('keysBtn.addEventListener')]
    assert "keysBtn.setAttribute('aria-label', 'Encryption keys')" in block
    assert "label.textContent = 'Keys'" in block
    assert 'innerHTML' not in block


def test_the_theme_buttons_name_follows_the_theme():
    update = SETTINGS[SETTINGS.index('function updateThemeIcon()'):]
    update = update[:update.index('\n}\n')]
    assert "setAttribute('aria-label'" in update
    assert 'Switch to dark theme' in update and 'Switch to light theme' in update


def test_the_mic_keeps_its_name_and_hidden_glyph_while_recording():
    messaging = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'messaging.js').read_text(encoding='utf-8')
    state = messaging[messaging.index('function setVoiceButtonState('):]
    state = state[:state.index('\n}\n')]
    assert 'voiceBtn.textContent' not in state, 'that would drop the aria-hidden wrapper'
    assert "'Record a voice message'" in state and "'Stop and send recording'" in state
