"""The send_image handler: the feature the whole project is about.

Its individual pieces were covered -- share generation, HMAC, Share 2
stripping -- but the handler that orchestrates them had no test at all. These
drive it over a real socket and check the claims the project actually makes:

  * Share 1 XOR Share 2 returns the original, bit for bit
  * the recipient gets Share 2 live and the sender never does
  * the sender never receives the capability token, so cannot reconstruct
  * the HMAC recorded covers the bytes on disk, not the array in memory
  * Share 2 never reaches the database
"""

import base64
import hmac as hmac_mod
import os

import cv2
import numpy as np
import pytest

from app import app as flask_app, socketio
from app.ai_security import hmac_image, sha256_image

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def last_message(client):
    got = events(client, 'new_message')
    return got[-1]['args'][0] if got else None


@pytest.fixture(autouse=True)
def _clean():
    _received.clear()
    yield
    _received.clear()


def png_data_url(image):
    ok, buf = cv2.imencode('.png', image)
    assert ok
    return 'data:image/png;base64,' + base64.b64encode(buf.tobytes()).decode()


def sample_image(size=64, seed=7):
    """A grayscale image with structure, so a failed reconstruction is obvious."""
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 200, size=(size, size), dtype=np.uint8)
    cv2.rectangle(img, (10, 10), (size - 10, size - 10), 0, 2)
    cv2.line(img, (0, 0), (size - 1, size - 1), 255, 2)
    return img


@pytest.fixture
def pair(store):
    """Two signed-in sockets, both online -- Share 2 is delivered live."""
    store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')

    a = socketio.test_client(flask_app)
    a.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    alice = events(a, 'auth_success')[0]['args'][0]['user']

    b = socketio.test_client(flask_app)
    b.emit('login', {'username': 'bob', 'password': 'correct-horse-2'})
    bob = events(b, 'auth_success')[0]['args'][0]['user']

    _received.clear()
    a.get_received()
    b.get_received()
    yield a, b, alice, bob
    for sock in (a, b):
        try:
            sock.disconnect()
        except Exception:
            pass          # a test may have disconnected it already


def stored_extra(store, message, viewer_id):
    """The server-side record. client_message deliberately withholds the
    stored filename and the HMAC tag, so tests read them from storage."""
    return store.get_message_for_user(message['id'], viewer_id)['extra']


def share_from_disk(store, message, viewer_id):
    path = os.path.join(store.shares_folder,
                        stored_extra(store, message, viewer_id)['share1_filename'])
    return cv2.imread(path, cv2.IMREAD_GRAYSCALE)


def share2_from_payload(payload):
    raw = base64.b64decode(payload['extra']['share2_live'].split(',', 1)[1])
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)


# ----------------------------------------------------------------------
# The claim the project is built on
# ----------------------------------------------------------------------

def test_share1_xor_share2_returns_the_original(pair, store):
    """If this fails, nothing else about the project matters."""
    a, b, alice, bob = pair
    original = sample_image()

    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(original)})

    delivered = last_message(b)
    assert delivered is not None, 'recipient received no message'

    share1 = share_from_disk(store, delivered, bob['id'])
    share2 = share2_from_payload(delivered)
    reconstructed = cv2.bitwise_xor(share1, share2)

    assert np.array_equal(reconstructed, original), 'reconstruction did not match'


def test_neither_share_alone_reveals_the_image(pair, store):
    """Each share on its own must look like noise, not a faint original."""
    a, b, alice, bob = pair
    original = sample_image()

    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(original)})
    delivered = last_message(b)

    for share in (share_from_disk(store, delivered, bob['id']), share2_from_payload(delivered)):
        # A share that leaked structure would correlate with the original.
        corr = np.corrcoef(share.ravel().astype(float),
                           original.ravel().astype(float))[0, 1]
        assert abs(corr) < 0.1, f'share correlates with the original: {corr:.3f}'


# ----------------------------------------------------------------------
# Who gets what
# ----------------------------------------------------------------------

def test_the_recipient_gets_share2_live(pair):
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})

    delivered = last_message(b)
    assert delivered['extra'].get('share2_live', '').startswith('data:image/png;base64,')


def test_the_sender_never_receives_share2(pair):
    """The sender already has the original; handing them Share 2 as well would
    put both halves in one place for no reason."""
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})

    own = last_message(a)
    assert own is not None, 'sender saw no echo of their own message'
    assert not own['extra'].get('share2_live')


def test_the_sender_never_receives_the_capability_token(pair):
    """Without the token the sender cannot fetch Share 1 back, so they cannot
    reconstruct from their own copy either."""
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})

    own = last_message(a)
    assert 'share1_token' not in own['extra']
    assert not own['extra'].get('share1_url')
    assert not own['extra'].get('share1_analysis_url')


def test_the_recipient_gets_a_usable_analysis_url(pair, store):
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})

    delivered = last_message(b)
    url = delivered['extra'].get('share1_analysis_url', '')
    assert url and 'token=' in url

    client = flask_app.test_client()
    body = client.get(url).get_json()
    assert body['ok'] is True
    assert body['integrity']['hmac_match'] is True


# ----------------------------------------------------------------------
# Integrity recorded at rest
# ----------------------------------------------------------------------

def test_the_hmac_covers_the_bytes_actually_written_to_disk(pair, store):
    """Tagging the in-memory array instead would leave PNG round-tripping
    outside the tag, and the recipient's check would be meaningless."""
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})

    delivered = last_message(b)
    on_disk = share_from_disk(store, delivered, bob['id'])
    recorded = stored_extra(store, delivered, bob['id'])

    expected = hmac_image(on_disk, flask_app.config['SECRET_KEY'])
    assert hmac_mod.compare_digest(recorded['share1_hmac'], expected)
    assert recorded['share1_sha256'] == sha256_image(on_disk)


def test_tampering_with_the_stored_share_breaks_the_recorded_hmac(pair, store):
    """The property the whole tamper-detection story rests on."""
    from app.ai_security.attacks import tamper_share

    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})
    delivered = last_message(b)

    path = os.path.join(store.shares_folder,
                        stored_extra(store, delivered, bob['id'])['share1_filename'])
    cv2.imwrite(path, tamper_share(cv2.imread(path, cv2.IMREAD_GRAYSCALE),
                                   attack='block', strength=0.4, seed=3))

    client = flask_app.test_client()
    body = client.get(delivered['extra']['share1_analysis_url']).get_json()
    assert body['integrity']['hmac_match'] is False
    assert body['integrity']['exact_tamper_detected'] is True


def test_share2_never_reaches_the_database(pair, store):
    """Delivered live to the recipient and nowhere else."""
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})
    delivered = last_message(b)

    stored = store.get_message_for_user(delivered['id'], bob['id'])
    assert 'share2_live' not in stored['extra']
    assert not any('share2' in key for key in stored['extra'])


def test_each_send_uses_a_fresh_mask(pair, store):
    """Reusing a mask across images is the classic two-time-pad mistake: XOR
    the two Share 1s and both originals leak."""
    a, b, alice, bob = pair
    shares = []
    for seed in (1, 2):
        a.emit('send_image', {'recipient_id': bob['id'],
                              'image': png_data_url(sample_image(seed=seed))})
        shares.append(share_from_disk(store, last_message(b), bob['id']))

    assert not np.array_equal(shares[0], shares[1])


# ----------------------------------------------------------------------
# Refusals
# ----------------------------------------------------------------------

def test_an_anonymous_socket_cannot_send(store):
    sock = socketio.test_client(flask_app)
    sock.emit('send_image', {'recipient_id': 'anyone', 'image': png_data_url(sample_image())})

    assert events(sock, 'image_error')
    assert events(sock, 'new_message') == []
    sock.disconnect()


def test_sending_to_an_unknown_account_is_refused(pair):
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': 'no-such-id', 'image': png_data_url(sample_image())})

    assert events(a, 'image_error')


def test_an_offline_recipient_is_refused_rather_than_losing_share2(pair, store):
    """Share 2 is never persisted, so there is nowhere to leave it. Failing
    loudly beats writing a share the recipient can never open."""
    a, b, alice, bob = pair
    b.disconnect()

    a.emit('send_image', {'recipient_id': bob['id'], 'image': png_data_url(sample_image())})

    assert 'online' in events(a, 'image_error')[-1]['args'][0]['error']
    assert os.listdir(store.shares_folder) == [], 'an unusable share was left on disk'


@pytest.mark.parametrize('payload', [
    'not-a-data-url',
    'data:text/plain;base64,aGVsbG8=',
    'data:image/gif;base64,R0lGODlhAQABAAAAACw=',
    '',
])
def test_junk_payloads_are_refused(pair, payload):
    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': payload})

    assert events(a, 'image_error'), f'accepted: {payload[:30]}'
    assert events(b, 'new_message') == []


def test_an_oversized_image_is_refused(pair, store):
    from app.routes import MAX_IMAGE_BYTES
    oversized = 'data:image/png;base64,' + base64.b64encode(
        b'\x00' * (MAX_IMAGE_BYTES + 1024)).decode()

    a, b, alice, bob = pair
    a.emit('send_image', {'recipient_id': bob['id'], 'image': oversized})

    assert events(a, 'image_error')
    assert os.listdir(store.shares_folder) == []


# ----------------------------------------------------------------------
# Reactions
#
# The server accepted react_message and broadcast message_reactions long
# before any client sent or listened for either. These cover the round trip
# and the removal path, which the route used to reject before it could reach
# set_reaction.
# ----------------------------------------------------------------------

def test_a_reaction_reaches_both_sides(pair, store):
    a, b, alice, bob = pair
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'react to me'})
    msg = last_message(b)

    b.emit('react_message', {'message_id': msg['id'], 'emoji': '\U0001F44D'}) 

    for sock in (a, b):
        payload = events(sock, 'message_reactions')[-1]['args'][0]
        assert payload['reactions']['\U0001F44D'] == [bob['id']]


def test_a_reaction_can_be_taken_back(pair, store):
    """An empty emoji clears it. The route used to drop that before
    set_reaction saw it, so a reaction could be added and never removed."""
    a, b, alice, bob = pair
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'react to me'})
    msg = last_message(b)

    b.emit('react_message', {'message_id': msg['id'], 'emoji': '\U0001F44D'})
    b.emit('react_message', {'message_id': msg['id'], 'emoji': ''})

    assert events(b, 'message_reactions')[-1]['args'][0]['reactions'] == {}


def test_one_reaction_per_person(pair, store):
    a, b, alice, bob = pair
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'react to me'})
    msg = last_message(b)

    b.emit('react_message', {'message_id': msg['id'], 'emoji': '\U0001F44D'})
    b.emit('react_message', {'message_id': msg['id'], 'emoji': '\U0001F602'})

    reactions = events(b, 'message_reactions')[-1]['args'][0]['reactions']
    assert reactions == {'\U0001F602': [bob['id']]}


def test_an_emoji_outside_the_allowed_set_is_ignored(pair, store):
    a, b, alice, bob = pair
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'react to me'})
    msg = last_message(b)

    b.emit('react_message', {'message_id': msg['id'], 'emoji': '<img src=x onerror=alert(1)>'})

    assert events(b, 'message_reactions') == []
