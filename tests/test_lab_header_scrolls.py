"""/lab's header scrolls away instead of covering the page.

It was position: sticky, carrying the full title and a two-line subtitle:
about 230px pinned over a 600px laptop viewport, and more on a phone where
the title wraps. Anything tabbed to under it was hidden (WCAG 2.4.11 Focus
Not Obscured), and every result, table and chart had a third of the screen
to work with. The page is one long column read top to bottom; the header
has nothing a user needs while reading further down, so it scrolls with the
page.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'app' / 'static' / 'css' / 'rx_lab.css').read_text(encoding='utf-8')


def test_the_header_is_not_pinned():
    rule = re.search(r'\n\.lab-header\s*\{([^}]*)\}', CSS).group(1)
    declarations = re.sub(r'/\*.*?\*/', '', rule, flags=re.S)
    assert not re.search(r'position:\s*(sticky|fixed)', declarations)
