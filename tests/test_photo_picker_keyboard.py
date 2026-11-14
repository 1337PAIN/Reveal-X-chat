"""Changing your photo works from the keyboard.

Both ways in were <label>s for a file input with the hidden attribute: the
sidebar avatar (a label wrapping the input) and "Change Photo" in settings
(a label pointing at it). A label is not focusable and a hidden input is
not either, so Tab never reached them and a keyboard user had no way to
change their photo. Both are now <button type="button">s that open the
file chooser; the inputs stay hidden.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
SETTINGS = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'settings.js').read_text(encoding='utf-8')
CSS = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'app' / 'static' / 'css').glob('*.css'))


def test_the_sidebar_avatar_is_a_button():
    tag = re.search(r'<[a-z]+[^>]*class="profile-picker"[^>]*>', INDEX).group(0)
    assert tag.startswith('<button') and 'type="button"' in tag
    assert 'aria-label="Change profile photo"' in tag
    picker = INDEX[INDEX.index(tag):]
    picker = picker[:picker.index('</button>')]
    # An input inside a button is invalid: it sits beside it instead.
    assert '<input' not in picker
    assert re.search(r'<input type="file" id="profileImageInput"[^>]*hidden', INDEX)


def test_change_photo_in_settings_is_a_button():
    assert '<label for="settingsProfileInput"' not in INDEX
    assert re.search(r'<button type="button" id="settingsProfileBtn" class="btn btn-secondary">Change photo</button>', INDEX)
    assert "settingsProfileBtn.addEventListener('click', () => settingsProfileInput.click())" in SETTINGS


def test_the_avatar_button_looks_like_the_avatar():
    rule = re.search(r'\n\.profile-picker\s*\{([^}]*)\}', CSS).group(1)
    for reset in ('background: none', 'border: 0', 'padding: 0'):
        assert reset in rule, reset


def test_the_avatar_button_is_big_enough_to_tap():
    blocks = re.findall(r'@media \(pointer: coarse\) \{(.*?)\n\}', CSS, re.S)
    assert any('.profile-picker' in b and 'min-width: 44px' in b and 'min-height: 44px' in b for b in blocks)
