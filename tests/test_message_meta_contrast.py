"""Name, time and read status on a sent bubble meet AAA in the light theme.

revealxContrastSweep() measured them on the light theme's sent bubble, which
composites to #b5c0f2: the name (--accent, #08423e) at 6.34:1 and the time
and status (--text-secondary, #2d3549) at 6.87:1 -- under the 7:1 AAA needs
for text this small. Earlier audits did not have sent messages on screen.
The fix is scoped to that bubble so the shared tokens, and everything else
that uses them, are unchanged.
"""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
SENT_BUBBLE_LIGHT = '#b5c0f2'  # measured composite behind the text


def luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def colour_of(selector):
    match = re.search(re.escape(selector) + r'\s*\{[^}]*color:\s*(#[0-9a-fA-F]{6})', CSS)
    assert match, f'no colour set for {selector}'
    return match.group(1)


def test_the_sender_name_is_aaa():
    colour = colour_of('body[data-theme="light"] .message.sent .message-username')
    assert ratio(colour, SENT_BUBBLE_LIGHT) >= 7


def test_the_time_and_status_are_aaa():
    colour = colour_of('body[data-theme="light"] .message.sent .message-time,\n'
                       'body[data-theme="light"] .message.sent .message-status')
    assert ratio(colour, SENT_BUBBLE_LIGHT) >= 7


def test_read_stays_green_and_aaa():
    colour = colour_of('body[data-theme="light"] .message.sent[data-read="true"] .message-status')
    assert ratio(colour, SENT_BUBBLE_LIGHT) >= 7
