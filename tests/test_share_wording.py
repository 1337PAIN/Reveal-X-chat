"""Image shares are described as what they are, and revoking is not an alarm.

"Encrypt & Send" and "encrypted shares" described a visual-cryptography
split; the sender's bubble said "The sender cannot reconstruct" to the
sender; and Revoke was a full-width red button on every share the user had
sent, styled like an error. The bubble now says who can rebuild the image and
until when it can be revoked, and Revoke is a secondary button that asks
first (askConfirm in render.js, tests/test_no_native_dialogs.py).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
RENDER = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'render.js').read_text(encoding='utf-8')
IMAGES = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'images.js').read_text(encoding='utf-8')
MESSAGING_JS = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'messaging.js').read_text(encoding='utf-8')
COMMON = (ROOT / 'app' / 'handlers' / 'common.py').read_text(encoding='utf-8')


def test_the_send_button_says_split():
    assert '>Split &amp; Send</button>' in INDEX or '>Split & Send</button>' in INDEX
    assert "confirmImageBtn.textContent = 'Split & Send'" in IMAGES
    assert 'Encrypt & Send' not in INDEX + IMAGES


def test_shares_are_called_image_shares():
    assert "content = 'Image share'" in COMMON
    assert "'Image share'" in MESSAGING_JS
    for old in ('encrypted shares', 'Encrypted image', 'Sent an encrypted share'):
        assert old not in RENDER + MESSAGING_JS + COMMON, old


def test_the_senders_bubble_says_who_can_rebuild_it():
    assert "`Only ${recipientName} can rebuild this image. You can revoke it until they open it.`" in RENDER
    # The name is another user's input: it goes in as text, not into the
    # bubble's markup template.
    assert '${statusMessage}' not in RENDER
    assert "querySelector('.share-status').textContent = statusMessage" in RENDER
    assert 'The sender cannot reconstruct' not in RENDER


def test_revoke_is_a_secondary_button():
    button = re.search(r'<button class="share-action[^"]*"[^>]*data-action="revoke"[^>]*>([^<]*)</button>', RENDER)
    assert button, 'no revoke button'
    assert 'btn-danger' not in button.group(0)
    assert 'secondary' in button.group(0)
    assert button.group(1) == 'Revoke'
