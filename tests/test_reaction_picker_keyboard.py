"""The reaction picker can be used from the keyboard.

Found in the Phase 2 keyboard-only walkthrough: Enter on "Add a reaction"
opened the picker but left focus on the button, and the next Tab went to
"More actions" -- the picker was appended at the end of the bubble, after
it. Nothing said the button opened anything (no aria-expanded), and Escape
left the picker open. It now works the way the message menu does
(dialogs.js wireMenu): focus moves to the first reaction, the arrow keys
move between them, and choosing one or pressing Escape closes the picker
and puts focus back on the button.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDER = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'render.js').read_text(encoding='utf-8')
REACTIONS = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'reactions.js').read_text(encoding='utf-8')


def picker_source():
    start = REACTIONS.index('export function toggleReactionPicker(')
    return REACTIONS[start:REACTIONS.index('\n}\n', start)]


def test_every_react_button_says_it_opens_something():
    buttons = re.findall(r'<button class="btn-react"[^>]*>', RENDER)
    assert len(buttons) == 3
    for button in buttons:
        assert 'aria-haspopup="true"' in button
        assert 'aria-expanded="false"' in button


def test_opening_moves_focus_in_and_says_so():
    source = picker_source()
    assert "setAttribute('aria-expanded', 'true')" in source
    assert '.focus()' in source
    assert "setAttribute('role', 'group')" in source
    assert "setAttribute('aria-label', 'Reactions')" in source


def test_escape_and_arrows_work_and_focus_comes_back():
    source = picker_source()
    assert "'Escape'" in source
    assert "'ArrowRight'" in source and "'ArrowLeft'" in source
    assert 'closeReactionPicker(true)' in source
    close = REACTIONS[REACTIONS.index('function closeReactionPicker('):]
    close = close[:close.index('\n}\n')]
    assert "setAttribute('aria-expanded', 'false')" in close
    assert 'button.focus()' in close
