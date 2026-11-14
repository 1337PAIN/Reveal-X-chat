"""A bubble's reaction picker and menu draw above the next bubble.

Found while taking the Phase 2 screenshots. Each bubble has a
backdrop-filter, which makes it its own stacking context, so the picker's
z-index only counted inside its own bubble. When the picker flips below a
bubble near the top of the chat (reactions.js adds .below), the next bubble
was painted over it: a hit test in the middle of the picker landed on the
next bubble's sender name, and the reactions could not be clicked. The
bubble holding an open picker or menu is now lifted above its neighbours.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'app' / 'static' / 'css' / 'settings.css').read_text(encoding='utf-8')


def test_a_bubble_with_an_open_popover_is_lifted():
    rule = re.search(r'([^{}]*:has\([^{}]*reaction-picker[^{}]*)\{([^}]*)\}', CSS)
    assert rule, 'no rule lifting the bubble that holds a picker'
    selectors, body = rule.group(1), rule.group(2)
    assert '.message:has(> .reaction-picker)' in selectors
    assert '.message-menu:not([hidden])' in selectors
    z = re.search(r'z-index:\s*(\d+)', body)
    assert z and int(z.group(1)) > 0
