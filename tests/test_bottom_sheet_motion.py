"""On a phone, dialogs rest on screen and slide in only when motion is wanted.

Found while taking the Phase 2 phone screenshots. Under 560px every dialog
becomes a bottom sheet whose resting position was translateY(100%) --
off the bottom of the screen -- with an animation carrying it up to 0. So
the dialog was only visible if that animation played: with no frames (a
background tab, as here) the delete confirmation had focus on its Cancel
button but sat entirely below the viewport, and the slide ignored
prefers-reduced-motion. The sheet now rests at 0, the animation starts it
from below, and reduced motion turns the animation off.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'app' / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')


def sheet_rule():
    block = CSS[CSS.index('/* Mobile Bottom Sheet Transform for Modals */'):]
    block = block[:block.index('@keyframes slideUp')]
    return re.search(r'\.modal-content\s*\{([^}]*)\}', block).group(1)


def test_the_sheet_rests_on_screen():
    declarations = re.sub(r'/\*.*?\*/', '', sheet_rule(), flags=re.S)
    assert 'translateY(100%)' not in declarations
    assert 'forwards' not in declarations


def test_the_slide_starts_below_and_ends_in_place():
    keyframes = CSS[CSS.index('@keyframes slideUp'):]
    keyframes = keyframes[:keyframes.index('\n}\n')]
    assert re.search(r'from\s*\{\s*transform:\s*translateY\(100%\)', keyframes)


def test_reduced_motion_turns_the_slide_off():
    found = False
    for match in re.finditer(r'@media [^{]*\(prefers-reduced-motion: reduce\) \{', CSS):
        body = CSS[match.end():CSS.index('\n}\n', match.end())]
        if re.search(r'\.modal-content\s*\{[^}]*animation:\s*none', body):
            found = True
    assert found
