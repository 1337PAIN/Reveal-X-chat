"""White initials and unread counts sit on a gradient dark enough to read.

Avatar initials and the unread badge were white on a translucent light-blue
to aqua gradient (glass.css). Composited over the page that is 1.4:1 to
3.0:1 -- below even AA's 4.5:1 for text this small. The contrast sweep never
saw it: the initials are aria-hidden, and the badge only exists while a
message is unread. The gradient is now opaque deep blue to deep teal, where
white is 10.4:1 and 7.6:1 at its two ends, in both themes.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLASS = (ROOT / 'app' / 'static' / 'css' / 'glass.css').read_text(encoding='utf-8')


def luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def ratio_with_white(hex_colour):
    return 1.05 / (luminance(hex_colour) + 0.05)


def gradient(selector):
    rule = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', GLASS).group(1)
    background = re.search(r'background:\s*linear-gradient\(([^;]*)\);', rule).group(1)
    assert 'rgba' not in background, f'{selector}: a translucent stop depends on what is behind it'
    return re.findall(r'#([0-9a-fA-F]{6})', background)


def test_avatar_initials_are_readable():
    stops = gradient('.avatar-dot')
    assert len(stops) == 2
    assert min(ratio_with_white(s) for s in stops) >= 7


def test_unread_counts_are_readable():
    stops = gradient('.unread-badge')
    assert len(stops) == 2
    assert min(ratio_with_white(s) for s in stops) >= 7
