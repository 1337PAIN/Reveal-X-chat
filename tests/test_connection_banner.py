"""The connection banner is wired to the socket and to auth_required.

The wording is tested in tests/js/connection.test.mjs. This checks the page
has the banner as a polite status region and that the three moments that
change it -- disconnect, a session back (auth_success), and a session that
could not be resumed (showSignIn after auth_required) -- all update it.
"""

import re
from pathlib import Path

from chat_source import chat_source

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
CHAT = chat_source()


def handler(marker):
    start = CHAT.index(marker)
    return CHAT[start:CHAT.index('\n});', start)]


def test_the_banner_is_a_polite_status_region():
    tag = re.search(r'<div[^>]*id="connectionBanner"[^>]*>', INDEX).group(0)
    assert 'role="status"' in tag and 'aria-live="polite"' in tag and 'hidden' in tag
    assert INDEX.index('js/connection.js') < INDEX.index('js/chat/main.js')


def test_disconnect_and_reconnect_update_it():
    assert 'updateConnectionBanner(' in handler("socket.on('disconnect'")
    assert 'updateConnectionBanner(' in handler("socket.on('connect'")
    assert 'updateConnectionBanner(' in handler("socket.on('auth_success'")


def test_a_failed_resume_shows_signed_out():
    sign_in = CHAT[CHAT.index('function showSignIn('):]
    sign_in = sign_in[:sign_in.index('\n}\n')]
    assert 'signedOut = true' in sign_in.replace(' ', ' ')
    assert 'updateConnectionBanner(' in sign_in
