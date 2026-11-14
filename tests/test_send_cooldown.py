"""The Send button keeps its name during the cooldown.

After each message the label was replaced by a countdown ("2s", "1s") and
the button was disabled. A screen reader then announced a button called
"2s", and disabling it threw keyboard focus out of the composer. Now the
label stays "Send", the cooldown is aria-disabled plus a visual state, and
a press during it is explained instead of ignored.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESSAGING = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'messaging.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'app' / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')


def send_message_body():
    body = MESSAGING[MESSAGING.index('async function sendMessage()'):]
    return body[:body.index('\n}\n')]


def test_the_label_is_never_replaced_by_a_countdown():
    body = send_message_body()
    assert not re.search(r'sendBtn\.textContent\s*=', body)
    assert 'sendBtn.disabled = true' not in body, 'disabling it drops keyboard focus'


def test_the_cooldown_is_aria_disabled_and_visible():
    body = send_message_body()
    assert "sendBtn.setAttribute('aria-disabled', 'true')" in body
    assert "sendBtn.classList.add('cooling')" in body
    assert "sendBtn.removeAttribute('aria-disabled')" in MESSAGING
    assert re.search(r'\.btn-send\.cooling\s*\{', STYLE)
    assert re.search(r'@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.btn-send\.cooling', STYLE)
