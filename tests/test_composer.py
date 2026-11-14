"""The composer's limit matches the server's, and says so near the end.

maxlength was 500 while the server's MAX_TEXT_LENGTH and the client's
MAX_MESSAGE_LENGTH were both 2000, so the field cut messages off silently at
a quarter of what was allowed. The chat search carried a `debounce`
attribute, which is not HTML and did nothing; the debouncing is in JS.
"""

import re
from pathlib import Path

from app.handlers import common

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
CORE = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'core.js').read_text(encoding='utf-8')
MESSAGING = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'messaging.js').read_text(encoding='utf-8')


def tag(element_id):
    return re.search(rf'<(?:input|span|textarea)[^>]*\bid="{element_id}"[^>]*>', INDEX, re.S).group(0)


def test_the_field_allows_what_the_server_allows():
    assert f'maxlength="{common.MAX_TEXT_LENGTH}"' in tag('messageInput')
    client = int(re.search(r'MAX_MESSAGE_LENGTH = (\d+)', CORE).group(1))
    assert client == common.MAX_TEXT_LENGTH


def test_the_counter_is_present_and_wired():
    counter = tag('composerCounter')
    assert 'hidden' in counter
    assert 'revealxComposer.counterState(' in MESSAGING
    assert INDEX.index('js/composer.js') < INDEX.index('js/chat/main.js')


def test_the_search_has_no_made_up_attribute():
    assert 'debounce=' not in INDEX


def test_the_longest_message_the_field_accepts_fits_once_encrypted():
    """2,000 four-byte characters, sealed with AES-GCM and base64'd, must be
    under the ciphertext limit, or the server refuses what the field allowed."""
    import base64
    plaintext = ('\U0001F510' * common.MAX_TEXT_LENGTH).encode('utf-8')
    ciphertext = base64.b64encode(plaintext + bytes(16))
    assert len(ciphertext) <= common.MAX_CIPHERTEXT_LENGTH
