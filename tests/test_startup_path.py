"""Nothing the app cannot start without may come from somebody else's server.

The symptom was a page that rendered perfectly and then responded to nothing,
every time, until the user pressed Ctrl+F5. The cause was two blocking
third-party scripts in the critical path and no fallback for either:

    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js">
    <script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.6/purify.min.js">

chat.js opens with `const socket = io();`. If that CDN was slow, blocked or
failed, `io` was undefined, the statement threw at the top of the file, and
every event handler below it never bound. Nothing was logged where a user
would see it. A hard refresh bypasses the service worker and the HTTP cache
and fetches the script cleanly, which is why it looked like a cache bug.

The DOMPurify URL was worse: it was wrong. `dompurify@3.0.6/purify.min.js`
404s -- the file is at `dompurify@3.0.6/dist/purify.min.js` -- so the client
side sanitiser the README and the report both describe had never loaded once.
cleanHTML() fails closed to escapeHtml(), which is why nothing looked broken.

Both are vendored now. These tests keep them that way.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / 'app' / 'templates'
STATIC = ROOT / 'app' / 'static'

VENDORED = {
    'vendor/socketio/socket.io.min.js': ('Socket.IO', 20_000),
    'vendor/dompurify/purify.min.js': ('DOMPurify', 10_000),
}


def templates():
    return sorted(TEMPLATES.glob('*.html'))


@pytest.mark.parametrize('template', templates(), ids=lambda p: p.name)
def test_no_page_loads_a_script_from_another_origin(template):
    """A stylesheet from a CDN degrades the look. A *script* in the critical
    path is a single point of failure for the whole application, owned by
    somebody else."""
    markup = template.read_text(encoding='utf-8')
    external = re.findall(r'<script[^>]*\ssrc=["\'](https?:)?//[^"\']+', markup)
    assert not external, (
        f'{template.name} loads a script from another origin: {external}'
    )


@pytest.mark.parametrize('path,expected', list(VENDORED.items()),
                         ids=lambda v: v if isinstance(v, str) else '')
def test_the_vendored_library_is_present_and_is_what_it_claims(path, expected):
    name, min_size = expected
    blob = STATIC / path
    assert blob.is_file(), f'{path} is not vendored'
    text = blob.read_text(encoding='utf-8', errors='replace')
    assert len(text) > min_size, (
        f'{path} is {len(text)} bytes -- too small to be {name}. The previous '
        f'DOMPurify URL returned a 61-byte "could not find the file" message, '
        f'which is exactly what this guards against'
    )
    assert name.lower() in text[:400].lower(), (
        f'{path} does not carry the {name} banner'
    )


@pytest.mark.parametrize('path', list(VENDORED))
def test_the_vendored_library_is_served(client, path):
    response = client.get(f'/static/{path}')
    assert response.status_code == 200
    assert len(response.data) > 10_000


@pytest.mark.parametrize('path', list(VENDORED))
def test_the_vendored_library_is_precached(path):
    """Precached, so a network blip on a cold load cannot stop the app from
    starting -- which is the failure this whole module exists for."""
    worker = (STATIC / 'sw.js').read_text(encoding='utf-8')
    assert f'/static/{path}' in worker


def test_the_page_loads_both_libraries_locally(client):
    page = client.get('/').get_data(as_text=True)
    for path in VENDORED:
        assert f'/static/{path}' in page, f'the page does not load {path}'


def test_the_dead_dompurify_url_is_gone(client):
    """The old URL 404s. Anything still pointing at it is a sanitiser that
    silently is not there."""
    page = client.get('/').get_data(as_text=True)
    worker = (STATIC / 'sw.js').read_text(encoding='utf-8')
    for text in (page, worker):
        assert 'dompurify@3.0.6/purify.min.js' not in text


def test_chat_js_refuses_to_start_silently_without_socket_io():
    """`const socket = io()` with no guard is the line that turned a slow CDN
    into a page that responds to nothing."""
    chat = (STATIC / 'js' / 'chat.js').read_text(encoding='utf-8')
    guard = chat.index("typeof io === 'undefined'")
    use = chat.index('const socket = io();')
    assert guard < use, 'io is used before it is checked'
    between = chat[guard:use]
    assert 'startupFailure' in between, 'the failure is not shown to the user'
    assert 'throw' in between, 'execution continues into handlers that cannot work'


def test_the_page_carries_the_failure_banner(client):
    page = client.get('/').get_data(as_text=True)
    assert 'id="startupFailure"' in page
    assert 'id="startupFailureReload"' in page


def test_the_worker_never_resolves_undefined():
    """respondWith(undefined) is a TypeError that fails the request and logs
    nothing useful, so a transient cache miss became a hard failure."""
    worker = (STATIC / 'sw.js').read_text(encoding='utf-8')
    # Anchor on the fetch handler's fallback specifically: the install handler
    # has a .catch of its own, and matching that one tests nothing.
    catch = worker[worker.index('// Fallback to cache on network failure'):]
    assert 'Response.error()' in catch, (
        'the fallback path can still resolve undefined'
    )
    assert catch.count('Response.error()') >= 2, (
        'both the navigation and the sub-resource paths must return a real '
        'Response; the sub-resource one is what broke the app'
    )


def test_no_origin_is_cacheable_that_the_page_does_not_use():
    """A cacheable origin nothing loads from is dead configuration that
    outlives the thing it was for."""
    worker = (STATIC / 'sw.js').read_text(encoding='utf-8')
    block = worker[worker.index('const CACHEABLE_ORIGINS'):]
    block = block[:block.index('];')]
    for gone in ('cdn.socket.io', 'cdn.jsdelivr.net'):
        assert gone not in block, f'{gone} is still trusted but nothing loads from it'
