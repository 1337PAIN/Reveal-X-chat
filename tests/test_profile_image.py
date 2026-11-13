"""Profile photos: what the server stores and how the client shows it.

The server checked only the MIME type in the data-URL header, so a header
such as `data:image/png;x"><a href=...>;base64,` passed, was stored, and was
then interpolated into innerHTML as an <img src="...">: the quote ended the
attribute and the rest became markup in every contact's sidebar. The CSP
blocks injected *scripts*, not injected links or forms.

Now the server accepts only a strict data URL, decodes it with Pillow and
stores a fresh PNG it encoded itself; and the client builds the <img> with
createElement and sets .src, so nothing in the value can become markup.
"""

import base64
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from app import app as flask_app, socketio

JS = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js'
_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def png_bytes(size=(16, 16), mode='RGB', fmt='PNG'):
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30) if mode == 'RGB' else 128).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def sock(store):
    _received.clear()
    store.create_user('alice', 'correct-horse-1')
    client = socketio.test_client(flask_app)
    client.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    user = events(client, 'auth_success')[0]['args'][0]['user']
    yield client, user
    client.disconnect()


def stored(store, user):
    return store.get_user(user['id'])['profile_image'] or ''


@pytest.mark.parametrize('value', [
    'data:image/png;x"><a href="https://evil.example">;base64,' + base64.b64encode(png_bytes()).decode(),
    'data:image/png;base64,' + base64.b64encode(png_bytes()).decode() + '"><b>',
    'data:image/svg+xml;base64,' + base64.b64encode(b'<svg onload="x()"/>').decode(),
    'data:image/png;base64,' + base64.b64encode(b'not an image at all').decode(),
    'javascript:alert(1)',
    'data:image/gif;base64,' + base64.b64encode(png_bytes(fmt='GIF')).decode(),
])
def test_anything_but_a_real_png_jpeg_or_webp_is_refused(sock, store, value):
    client, user = sock
    client.emit('update_profile', {'profile_image': value})
    assert stored(store, user) == ''
    assert events(client, 'auth_error') or events(client, 'settings_error')


@pytest.mark.parametrize('fmt,mime', [('PNG', 'png'), ('JPEG', 'jpeg'), ('WEBP', 'webp')])
def test_a_real_image_is_stored_as_a_png_the_server_encoded(sock, store, fmt, mime):
    client, user = sock
    raw = png_bytes(fmt=fmt)
    client.emit('update_profile', {'profile_image': f'data:image/{mime};base64,' + base64.b64encode(raw).decode()})
    value = stored(store, user)
    assert re.fullmatch(r'data:image/png;base64,[A-Za-z0-9+/]+=*', value)
    decoded = Image.open(io.BytesIO(base64.b64decode(value.split(',', 1)[1])))
    assert decoded.format == 'PNG' and decoded.size == (16, 16)


def test_an_absurdly_large_image_is_refused_before_it_is_decoded(sock, store):
    client, user = sock
    raw = png_bytes(size=(5000, 5000), mode='L')
    client.emit('update_profile', {'profile_image': 'data:image/png;base64,' + base64.b64encode(raw).decode()})
    assert stored(store, user) == ''


def test_clearing_the_photo_still_works(sock, store):
    client, user = sock
    client.emit('update_profile', {'profile_image': 'data:image/png;base64,' + base64.b64encode(png_bytes()).decode()})
    client.emit('update_profile', {'profile_image': ''})
    assert stored(store, user) == ''


def test_no_script_interpolates_a_profile_image_into_markup():
    offenders = []
    for path in JS.rglob('*.js'):
        if 'vendor' in path.parts:
            continue
        source = path.read_text(encoding='utf-8')
        for literal in re.findall(r'`[^`]*`', source, re.S):
            if re.search(r'\$\{[^}]*profile_?[iI]mage[^}]*\}', literal):
                offenders.append(f'{path.relative_to(JS)}: {literal[:80]!r}')
    assert not offenders, offenders


def test_forms_may_only_post_to_this_origin(client):
    policy = client.get('/').headers['Content-Security-Policy']
    assert "form-action 'self'" in policy
