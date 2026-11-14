"""The header's Admin button says what its number means.

The pending-approval count is a badge inside the button, so the button was
read as "Admin 3" -- three of what? The badge is now hidden from screen
readers and the button's name carries the count in words: "Admin, 3
accounts waiting for approval". It still starts with the visible word
"Admin", so speech users can say what they see (WCAG 2.5.3).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
ADMIN = (ROOT / 'app' / 'static' / 'js' / 'admin.js').read_text(encoding='utf-8')


def test_the_badge_is_decoration_and_the_button_is_named():
    button = re.search(r'<button id="adminBtn"[^>]*>(.*?)</button>', INDEX, re.S)
    assert 'aria-label="Admin"' in button.group(0)
    badge = re.search(r'<span id="adminPendingBadge"[^>]*>', button.group(1)).group(0)
    assert 'aria-hidden="true"' in badge


def test_the_name_carries_the_count():
    render = ADMIN[ADMIN.index('function render(payload)'):]
    render = render[:render.index('\n    }\n')]
    assert "adminBtn.setAttribute('aria-label'" in render
    assert 'accounts waiting for approval' in ADMIN
    assert "'Admin, '" in ADMIN or '`Admin, ${' in ADMIN
