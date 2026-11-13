"""send_share: the server stores Share 1 and an envelope it cannot read.

The browser splits the image (app/static/js/vc-client.js, tested in
tests/js/vc-client.test.mjs). What reaches the server is Share 1 -- random
bytes, as a single-channel PNG -- and an E2EE v2 ciphertext carrying Share 2.
These tests hold the server to its half: it stores Share 1 byte for byte (the
recipient's end-to-end hash is over those bytes), keys its HMAC and runs its
detector as before, refuses anything that is not plausibly a share, and no
longer accepts a plaintext image at all.
"""

import base64
import hashlib
import os
import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import app as flask_app, socketio
from app import routes

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def png_b64(pixels):
    ok, buf = cv2.imencode('.png', pixels)
    assert ok
    return base64.b64encode(buf.tobytes()).decode('ascii')


def random_share(height=48, width=40, seed=3):
    return np.random.default_rng(seed).integers(0, 256, (height, width), dtype=np.uint8)


def structured_image(height=48, width=40):
    image = np.tile(np.arange(width, dtype=np.uint8) * 5, (height, 1))
    image[10:30, 8:24] = 230
    return image


def payload(recipient_id, share1=None, **overrides):
    body = {
        'recipient_id': recipient_id,
        'share1_png': png_b64(random_share() if share1 is None else share1),
        # Opaque to the server by design; any base64 stands in for the
        # AES-GCM ciphertext of the envelope.
        'envelope': base64.b64encode(os.urandom(512)).decode('ascii'),
        'iv': base64.b64encode(os.urandom(12)).decode('ascii'),
        'encrypted': True,
        'v': 2,
        'msg_uuid': str(uuid.uuid4()),
    }
    body.update(overrides)
    return body


@pytest.fixture
def pair(store):
    _received.clear()
    store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')
    socks, users = [], []
    for name, pw in (('alice', 'correct-horse-1'), ('bob', 'correct-horse-2')):
        s = socketio.test_client(flask_app)
        s.emit('login', {'username': name, 'password': pw})
        users.append(events(s, 'auth_success')[0]['args'][0]['user'])
        socks.append(s)
    for s in socks:
        s.get_received()
    _received.clear()
    yield socks[0], socks[1], users[0], users[1]
    for s in socks:
        try:
            s.disconnect()
        except Exception:
            pass


def stored_path(store, message_id, viewer_id):
    extra = store.get_message_for_user(message_id, viewer_id)['extra']
    return os.path.join(store.shares_folder, extra['share1_filename'])


def errors(sock):
    return [e['args'][0]['error'] for e in events(sock, 'image_error')]


# ----------------------------------------------------------------------
# The happy path
# ----------------------------------------------------------------------

def test_share1_is_stored_byte_for_byte(pair, store):
    a, b, alice, bob = pair
    body = payload(bob['id'])
    a.emit('send_share', body)

    message = events(b, 'new_message')[0]['args'][0]
    with open(stored_path(store, message['id'], bob['id']), 'rb') as handle:
        assert handle.read() == base64.b64decode(body['share1_png'])


def test_the_recipient_gets_the_envelope_and_the_capability(pair, store):
    a, b, alice, bob = pair
    body = payload(bob['id'])
    a.emit('send_share', body)

    message = events(b, 'new_message')[0]['args'][0]
    assert message['type'] == 'share'
    assert message['content'] == body['envelope']
    assert message['extra']['v'] == 2
    assert message['extra']['msg_uuid'] == body['msg_uuid']
    assert message['extra']['iv'] == body['iv']
    assert message['extra']['share1_url']


def test_the_sender_gets_no_capability(pair, store):
    a, b, alice, bob = pair
    a.emit('send_share', payload(bob['id']))
    mine = events(a, 'new_message')[0]['args'][0]
    assert not mine['extra']['share1_url']
    assert 'share1_token' not in repr(mine)


def test_the_download_is_the_uploaded_bytes(pair, store, client):
    """The recipient hashes what it downloads against the sender's hash, so
    the server must serve exactly what it was given."""
    a, b, alice, bob = pair
    body = payload(bob['id'])
    a.emit('send_share', body)
    url = events(b, 'new_message')[0]['args'][0]['extra']['share1_url']
    served = client.get(url).data
    assert hashlib.sha256(served).hexdigest() == \
        hashlib.sha256(base64.b64decode(body['share1_png'])).hexdigest()


def test_the_hmac_and_detector_still_run(pair, store, client):
    a, b, alice, bob = pair
    a.emit('send_share', payload(bob['id']))
    extra = events(b, 'new_message')[0]['args'][0]['extra']
    report = client.get(extra['share1_analysis_url']).get_json()
    assert report['ok'] and report['integrity']['hmac_match'] is True
    assert 'tampered' in report['ml']


def test_changing_the_stored_share_breaks_the_hmac(pair, store, client):
    a, b, alice, bob = pair
    a.emit('send_share', payload(bob['id']))
    message = events(b, 'new_message')[0]['args'][0]
    path = stored_path(store, message['id'], bob['id'])
    pixels = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    pixels[0, 0] ^= 1
    cv2.imwrite(path, pixels)
    report = client.get(message['extra']['share1_analysis_url']).get_json()
    assert report['integrity']['hmac_match'] is False


def test_the_recipient_need_not_be_online(pair, store):
    """Share 2 is encrypted, so it can be stored; the old split had to refuse
    an offline recipient because it could only hand Share 2 over live."""
    a, b, alice, bob = pair
    b.disconnect()
    a.emit('send_share', payload(bob['id']))
    assert not errors(a)
    later = store.get_conversation(bob['id'], alice['id'])
    assert later and later[-1]['type'] == 'share'


def test_a_repeated_msg_uuid_leaves_no_orphan_file(pair, store):
    a, b, alice, bob = pair
    body = payload(bob['id'])
    a.emit('send_share', body)
    before = set(os.listdir(store.shares_folder))
    a.emit('send_share', dict(body, share1_png=png_b64(random_share(seed=9))))
    assert set(os.listdir(store.shares_folder)) == before
    assert errors(a)


# ----------------------------------------------------------------------
# What the server refuses
# ----------------------------------------------------------------------

@pytest.mark.parametrize('share1,why', [
    (lambda: base64.b64encode(cv2.imencode('.png', cv2.cvtColor(random_share(), cv2.COLOR_GRAY2BGR))[1].tobytes()).decode(), 'three channels'),
    (lambda: base64.b64encode(cv2.imencode('.jpg', random_share())[1].tobytes()).decode(), 'not a PNG'),
    (lambda: png_b64(structured_image()), 'an image, not a share'),
    (lambda: png_b64(random_share(4, 4)), 'too small to be checked'),
    (lambda: png_b64(random_share(1700, 20)), 'too tall'),
    (lambda: '!!!not base64!!!', 'not base64'),
    (lambda: '', 'missing'),
])
def test_anything_that_is_not_plausibly_share1_is_refused(pair, store, share1, why):
    a, b, alice, bob = pair
    a.emit('send_share', payload(bob['id'], share1_png=share1()))
    assert not events(b, 'new_message'), why
    assert errors(a), why
    assert os.listdir(store.shares_folder) == [], f'{why}: a file was left behind'


def test_an_oversized_share_is_refused_before_decoding(pair, store, monkeypatch):
    a, b, alice, bob = pair
    decoded = []
    monkeypatch.setattr(routes.cv2, 'imdecode', lambda *x, **k: decoded.append(1))
    big = base64.b64encode(b'\x89PNG' + b'\0' * (routes.MAX_SHARE1_BYTES + 1)).decode()
    a.emit('send_share', payload(bob['id'], share1_png=big))
    assert errors(a) and not decoded


@pytest.mark.parametrize('overrides,why', [
    ({'envelope': ''}, 'no envelope'),
    ({'envelope': 'x' * (routes.MAX_SHARE_ENVELOPE_LENGTH + 1)}, 'envelope too large'),
    ({'v': 1}, 'not v2'),
    ({'msg_uuid': 'nope'}, 'bad uuid'),
    ({'iv': ''}, 'no iv'),
    ({'share2_live': 'data:image/png;base64,AAAA'}, 'Share 2 in the clear'),
    ({'share2_b64': 'AAAA'}, 'Share 2 in the clear'),
    ({'image': 'data:image/png;base64,AAAA'}, 'the plaintext image'),
])
def test_the_envelope_must_be_encrypted_v2_and_alone(pair, store, overrides, why):
    a, b, alice, bob = pair
    a.emit('send_share', payload(bob['id'], **overrides))
    assert not events(b, 'new_message'), why
    assert errors(a), why


def test_the_server_split_is_gone(pair, store, monkeypatch):
    """send_image took the picture itself. Nothing on the server decodes an
    uploaded image for chat any more; a stale page is told to reload."""
    a, b, alice, bob = pair
    decoded = []
    monkeypatch.setattr(routes.cv2, 'imdecode', lambda *x, **k: decoded.append(1))
    a.emit('send_image', {'recipient_id': bob['id'], 'image': 'data:image/png;base64,' + png_b64(structured_image())})
    assert not decoded
    assert not events(b, 'new_message')
    assert any('browser' in e.lower() for e in errors(a))
    assert not hasattr(routes, 'parse_image_data_url')
    handlers = Path(routes.__file__).resolve().parent / 'handlers'
    for module in handlers.glob('*.py'):
        assert 'REVEALX_LEGACY_SERVER_SPLIT' not in module.read_text(encoding='utf-8'), module.name


# ----------------------------------------------------------------------
# The browser half as wired into chat.js (the logic is in vc-client.js and
# tested in tests/js/vc-client.test.mjs; chat.js needs the page to run)
# ----------------------------------------------------------------------

def _chat():
    from pathlib import Path
    from chat_source import chat_source
    return chat_source()


def test_the_client_never_emits_the_image():
    chat = _chat()
    assert "socket.emit('send_image'" not in chat
    send = chat[chat.index('async function sendImageShare('):]
    send = send[:send.index('\n}\n')]
    emitted = send[send.index("socket.emit('send_share'"):]
    emitted = emitted[:emitted.index('});')]
    assert 'dataUrl' not in emitted and 'image:' not in emitted
    assert 'share1_png' in emitted and 'envelope: sealed.ciphertext' in emitted
    # Without keys there is no safe way to send Share 2, so it refuses.
    assert 'isReady()' in send and 'return false' in send


def test_the_recipient_hashes_share1_bytes_before_decoding_them():
    chat = _chat()
    opening = chat[chat.index('async function openReconstruction('):]
    hashed = opening.index('sha256Hex(share1Bytes)')
    assert hashed < opening.index('runLocalShareVerification(')
    assert 'envelope.share1_sha256' in opening[hashed:hashed + 300]


def test_the_panel_shows_all_four_checks():
    chat = _chat()
    panel = chat[chat.index('function renderTamperPanel('):]
    panel = panel[:panel.index('\n}\n')]
    for label in ('HMAC-SHA256', 'Server ML', 'In-browser ML', 'End-to-end SHA-256'):
        assert label in panel, label


def test_vc_client_is_loaded_and_precached():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'app'
    index = (root / 'templates' / 'index.html').read_text(encoding='utf-8')
    assert index.index('js/vc-client.js') < index.index('js/chat/main.js')
    assert "'/static/js/vc-client.js'" in (root / 'static' / 'sw.js').read_text(encoding='utf-8')


# ----------------------------------------------------------------------
# Carried over from the retired server-split tests
# ----------------------------------------------------------------------

def test_an_anonymous_socket_cannot_send_a_share(store):
    sock = socketio.test_client(flask_app)
    sock.emit('send_share', payload('anyone'))
    assert events(sock, 'auth_required')
    assert not events(sock, 'new_message')
    assert os.listdir(store.shares_folder) == []
    sock.disconnect()


def test_sending_to_an_unknown_account_is_refused(pair, store):
    a, b, alice, bob = pair
    a.emit('send_share', payload('no-such-id'))
    assert errors(a)
    assert os.listdir(store.shares_folder) == []


def test_nothing_the_server_stores_is_share2_in_the_clear(pair, store):
    """The storage-layer refusal (FORBIDDEN_EXTRA_KEYS) still stands, and the
    only form Share 2 now reaches the server in is the sealed envelope."""
    from app.models import FORBIDDEN_EXTRA_KEYS
    a, b, alice, bob = pair
    body = payload(bob['id'])
    a.emit('send_share', body)
    with store._connect() as db:
        row = db.execute("SELECT content, extra FROM messages WHERE type = 'share'").fetchone()
    assert row['content'] == body['envelope']
    for key in FORBIDDEN_EXTRA_KEYS:
        assert key not in row['extra']


def test_the_stored_share1_is_uniform_noise(pair, store):
    """What sits on disk carries nothing of the image: the server cannot even
    have chosen it, because the sender's browser did."""
    a, b, alice, bob = pair
    a.emit('send_share', payload(bob['id'], share1=random_share(64, 64, seed=21)))
    message = events(b, 'new_message')[0]['args'][0]
    pixels = cv2.imread(stored_path(store, message['id'], bob['id']), cv2.IMREAD_UNCHANGED)
    counts = np.bincount(pixels.ravel(), minlength=256)
    expected = pixels.size / 256
    assert ((counts - expected) ** 2 / expected).sum() < routes.SHARE_CHI_SQUARE_LIMIT
