"""Entrance animations stop when the user asks for reduced motion.

Found while checking the bottom-sheet fix: every new message bubble slid up
10px as it faded in, the offline banner slid down, and the activity flash
pulsed, with no prefers-reduced-motion override for any of them, so a chat
in full flow was in constant motion for a user who had asked for none. (The
spinner already had a substitute, a cross-fade, and keeps it.)
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'app' / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')


def reduced_motion_blocks():
    for match in re.finditer(r'@media [^{]*\(prefers-reduced-motion: reduce\)[^{]*\{', CSS):
        yield CSS[match.end():CSS.index('\n}\n', match.end())]


def turned_off(selector):
    pattern = re.compile(r'(^|[\s,])' + re.escape(selector) + r'\s*[,{][^}]*animation:\s*none', re.M)
    return any(pattern.search(block) for block in reduced_motion_blocks())


def test_message_bubbles_do_not_slide_in():
    assert turned_off('.message')


def test_the_offline_banner_does_not_slide_down():
    assert turned_off('.offline-banner')


def test_the_activity_flash_does_not_pulse():
    assert turned_off('.activity-flash')
