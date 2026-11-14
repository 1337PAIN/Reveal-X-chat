"""A bubble's meta row wraps instead of spilling out of the bubble.

Found in the Phase 2 phone screenshot at 375px. A share bubble's meta row
holds the "Opens once - 3 min left" chip, the time, "Sent", React and More
actions -- about 320px of content in a 245px row. With nowrap and
justify-content: flex-end it overflowed to the left, past the edge of the
bubble, and the chip was cut by it. The row now wraps onto a second line.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'app' / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')


def test_the_meta_row_wraps():
    rule = re.search(r'\n\.message-meta\s*\{([^}]*)\}', CSS).group(1)
    assert re.search(r'flex-wrap:\s*wrap', rule)
