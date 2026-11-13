"""Share 2 is held in memory and nowhere else on the client.

The server is forbidden from persisting Share 2 (FORBIDDEN_EXTRA_KEYS), but the
client then wrote it to sessionStorage under `revealx_share2_<id>` so it would
survive a reload. sessionStorage is readable by any script on the origin and
outlives logout for as long as the tab is open, so after signing out the next
person at the keyboard could pull Share 2 back out with one line in the
console. Combined with a still-unused Share 1 link, that is the image.

These tests keep it in an in-memory Map, and keep that Map emptied on every
path that is supposed to end access to it.
"""

import re
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js'


def shipped_scripts():
    return sorted(p for p in JS.rglob('*.js') if 'vendor' not in p.parts)


def chat_js():
    return (JS / 'chat.js').read_text(encoding='utf-8')


@pytest.mark.parametrize('script', shipped_scripts(), ids=lambda p: p.name)
def test_no_script_writes_share2_to_web_storage(script):
    source = script.read_text(encoding='utf-8')
    writes = re.findall(r'(?:session|local)Storage\.setItem\(([^;]*)', source)
    leaking = [w for w in writes if 'share2' in w.lower()]
    assert not leaking, f'{script.name} writes Share 2 to web storage: {leaking}'
    assert 'sessionStorage.setItem' not in source, (
        f'{script.name} writes to sessionStorage; nothing in the app needs to, '
        f'and the last thing that did was Share 2'
    )


def test_share2_is_kept_in_an_in_memory_map():
    source = chat_js()
    assert re.search(r'const share2Memory = new Map\(\);', source)
    display = source[source.index('function displayMessage(msg)'):]
    display = display[:display.index('\nfunction ')]
    assert 'share2Memory' in display, 'displayMessage does not use the in-memory store'


def _handler(source, marker):
    start = source.index(marker)
    # A handler ends at the next top-level statement.
    end = re.search(r'\n(?:socket\.on|function |const |let |if \()', source[start + 1:])
    return source[start:start + 1 + end.start()] if end else source[start:]


@pytest.mark.parametrize('marker', [
    'function logoutUser()',
    "socket.on('logout_success'",
    "socket.on('force_signed_out'",
    "socket.on('message_deleted'",
    "socket.on('share_revoked'",
])
def test_every_path_that_ends_access_forgets_share2(marker):
    body = _handler(chat_js(), marker)
    assert re.search(r'share2Memory\.(clear|delete)\(|forgetShare2\(', body), (
        f'{marker} does not drop Share 2 from memory'
    )


def test_clear_cache_forgets_share2():
    source = chat_js()
    start = source.index("clearCacheBtn.addEventListener('click'")
    body = source[start:start + 2500]
    assert 'share2Memory.clear()' in body


def test_copies_left_by_the_old_build_are_purged_on_load():
    """A tab that was open across the upgrade still holds the old entries."""
    source = chat_js()
    assert "startsWith('revealx_share2_')" in source
    assert 'sessionStorage.removeItem' in source
