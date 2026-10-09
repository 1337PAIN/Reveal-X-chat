"""Voice messages over Socket.IO, encrypted and plaintext."""

import base64

import pytest

from app import app as flask_app, socketio


def base64_payload(size=2048, fill=b'\x01'):
    return base64.b64encode(fill * size).decode('ascii')


def data_url(mime='audio/webm', size=2048):
    return f'data:{mime};base64,{base64_payload(size)}'


# get_received() empties the queue, so drained events are cached per client and
# accumulated. Tests can then call events() as often as they like.
_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


@pytest.fixture
def pair(store):
    """Two logged-in socket clients: (alice_client, bob_client, alice, bob)."""
    _received.clear()
    # Registration is request-and-approve now, so accounts are created
    # directly (create_user defaults to 'active') and then signed in.
    store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')

    alice_client = socketio.test_client(flask_app)
    alice_client.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    alice = events(alice_client, 'auth_success')[0]['args'][0]['user']

    bob_client = socketio.test_client(flask_app)
    bob_client.emit('login', {'username': 'bob', 'password': 'correct-horse-2'})
    bob = events(bob_client, 'auth_success')[0]['args'][0]['user']

    alice_client.get_received()
    bob_client.get_received()

    yield alice_client, bob_client, alice, bob

    alice_client.disconnect()
    bob_client.disconnect()


def test_plaintext_voice_is_delivered_and_labelled(pair, store):
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {'recipient_id': bob['id'], 'audio': data_url()})

    delivered = events(bob_client, 'new_message')
    assert len(delivered) == 1

    message = delivered[0]['args'][0]
    assert message['type'] == 'voice'
    assert message['content'].startswith('data:audio/webm;base64,')
    assert message['encrypted'] is False
    # No key was exchanged, so the UI must show it as unencrypted.
    assert message['is_legacy'] is True


def test_encrypted_voice_is_stored_as_ciphertext(pair, store):
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {
        'recipient_id': bob['id'],
        'audio': base64_payload(),
        'iv': 'MTIzNDU2Nzg5MDEy',
        'mime': 'audio/webm',
        'encrypted': True,
    })

    message = events(bob_client, 'new_message')[0]['args'][0]
    assert message['type'] == 'voice'
    assert message['encrypted'] is True
    assert message['is_legacy'] is False
    assert message['extra']['iv'] == 'MTIzNDU2Nzg5MDEy'
    assert message['extra']['mime'] == 'audio/webm'
    # The server holds ciphertext only: no audio container header.
    assert not message['content'].startswith('data:')

    stored = store.get_message_for_user(message['id'], bob['id'])
    assert stored['content'] == message['content']


def test_encrypted_voice_needs_an_iv(pair):
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {
        'recipient_id': bob['id'],
        'audio': base64_payload(),
        'mime': 'audio/webm',
        'encrypted': True,
    })

    assert events(bob_client, 'new_message') == []
    assert events(alice_client, 'message_error')[0]['args'][0]['error'] == 'Invalid encrypted payload'


@pytest.mark.parametrize('mime', ['audio/exe', 'application/octet-stream', 'text/plain', ''])
def test_unsupported_audio_types_are_rejected(pair, mime):
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {
        'recipient_id': bob['id'],
        'audio': base64_payload(),
        'iv': 'MTIzNDU2Nzg5MDEy',
        'mime': mime,
        'encrypted': True,
    })

    assert events(bob_client, 'new_message') == []
    assert events(alice_client, 'message_error')


def test_plaintext_voice_rejects_a_non_audio_data_url(pair):
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {
        'recipient_id': bob['id'],
        'audio': 'data:text/html;base64,' + base64_payload(16),
    })

    assert events(bob_client, 'new_message') == []
    assert events(alice_client, 'message_error')


def test_plaintext_voice_rejects_a_disguised_audio_type(pair):
    """A data URL claiming audio/ must still name an allowed container."""
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {
        'recipient_id': bob['id'],
        'audio': 'data:audio/x-evil;base64,' + base64_payload(16),
    })

    assert events(bob_client, 'new_message') == []
    assert events(alice_client, 'message_error')[0]['args'][0]['error'] == 'Unsupported audio type'


@pytest.mark.parametrize('encrypted', [True, False])
def test_oversized_voice_is_rejected(pair, encrypted):
    alice_client, bob_client, alice, bob = pair
    oversized = base64_payload(3 * 1024 * 1024)  # 3 MB, over the 2 MB cap

    payload = {'recipient_id': bob['id']}
    if encrypted:
        payload.update({'audio': oversized, 'iv': 'MTIzNDU2Nzg5MDEy',
                        'mime': 'audio/webm', 'encrypted': True})
    else:
        payload['audio'] = f'data:audio/webm;base64,{oversized}'

    alice_client.emit('send_voice', payload)

    assert events(bob_client, 'new_message') == []
    assert events(alice_client, 'message_error')[0]['args'][0]['error'] == 'Voice message is too large'


def test_encrypted_voice_cannot_be_forwarded(pair, store):
    """Ciphertext is bound to one conversation's derived key."""
    alice_client, bob_client, alice, bob = pair
    carol, error = store.create_user('carol', 'correct-horse-3')
    assert error is None

    alice_client.emit('send_voice', {
        'recipient_id': bob['id'],
        'audio': base64_payload(),
        'iv': 'MTIzNDU2Nzg5MDEy',
        'mime': 'audio/webm',
        'encrypted': True,
    })
    message = events(bob_client, 'new_message')[0]['args'][0]

    bob_client.emit('forward_message', {'message_id': message['id'], 'recipient_id': carol['id']})

    assert events(bob_client, 'message_error')[0]['args'][0]['error'] == 'Encrypted messages cannot be forwarded'


def test_voice_reply_preview_does_not_leak_audio(pair, store):
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {'recipient_id': bob['id'], 'audio': data_url()})
    voice = events(bob_client, 'new_message')[0]['args'][0]

    bob_client.emit('send_message', {
        'recipient_id': alice['id'],
        'message': 'got it',
        'reply_to': voice['id'],
    })

    # Alice also receives an echo of her own voice message, so take the latest.
    reply = events(alice_client, 'new_message')[-1]['args'][0]
    assert reply['content'] == 'got it'
    assert reply['extra']['reply_to']['content'] == 'Voice message'


def test_recipient_deleting_a_voice_message_only_affects_their_side(pair, store):
    """The recipient deleting must not remove the sender's copy."""
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {'recipient_id': bob['id'], 'audio': data_url()})
    message = events(bob_client, 'new_message')[0]['args'][0]

    bob_client.emit('delete_message', {'message_id': message['id']})

    # Bob is told to drop it; alice is told nothing.
    deleted = events(bob_client, 'message_deleted')[0]['args'][0]
    assert deleted['message_id'] == message['id']
    assert deleted['scope'] == 'self'
    assert events(alice_client, 'message_deleted') == []

    assert store.get_conversation(bob['id'], alice['id']) == []
    assert len(store.get_conversation(alice['id'], bob['id'])) == 1


def test_sender_deleting_a_voice_message_clears_both_sides(pair, store):
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_voice', {'recipient_id': bob['id'], 'audio': data_url()})
    message = events(bob_client, 'new_message')[0]['args'][0]

    alice_client.emit('delete_message', {'message_id': message['id']})

    # Both sides are told, and both transcripts are empty.
    assert events(alice_client, 'message_deleted')[0]['args'][0]['scope'] == 'everyone'
    assert events(bob_client, 'message_deleted')[0]['args'][0]['message_id'] == message['id']

    assert store.get_conversation(alice['id'], bob['id']) == []
    assert store.get_conversation(bob['id'], alice['id']) == []


def test_encrypted_text_round_trip_over_sockets(pair, store):
    """The text path stores ciphertext plus IV and nothing else."""
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('send_message', {
        'recipient_id': bob['id'],
        'message': 'Y2lwaGVydGV4dA==',
        'iv': 'MTIzNDU2Nzg5MDEy',
        'encrypted': True,
    })

    message = events(bob_client, 'new_message')[0]['args'][0]
    assert message['encrypted'] is True
    assert message['content'] == 'Y2lwaGVydGV4dA=='
    assert message['extra']['iv'] == 'MTIzNDU2Nzg5MDEy'


def test_public_key_registration_reaches_the_peer(pair):
    alice_client, bob_client, alice, bob = pair
    public_key = base64.b64encode(b'a fake spki public key').decode('ascii')

    alice_client.emit('register_public_key', {'public_key': public_key})

    assert events(alice_client, 'public_key_registered')[0]['args'][0]['public_key'] == public_key

    peers = events(bob_client, 'users_updated')[-1]['args'][0]['users']
    assert [u['public_key'] for u in peers if u['username'] == 'alice'] == [public_key]


def test_invalid_public_key_is_rejected(pair):
    alice_client, _, _, _ = pair

    alice_client.emit('register_public_key', {'public_key': 'not-base64!!!'})

    assert events(alice_client, 'public_key_registered') == []
    assert events(alice_client, 'auth_error')[0]['args'][0]['error'] == 'Invalid public key'


# ----------------------------------------------------------------------
# Cross-platform playback
#
# MediaRecorder picks a container per engine when it is not told which to
# use: Chrome gives WebM/Opus, Safari gives MP4/AAC. Each engine plays its
# own and may refuse the other's, and an <audio> element that cannot decode
# its source does not raise -- it loads, shows controls and plays silence.
# A voice message from Android arriving mute on an iPhone has no error
# anywhere to explain it, so these assertions stand in for the two browsers
# this suite cannot run.
# ----------------------------------------------------------------------

CHAT_JS = 'app/static/js/chat.js'


@pytest.fixture
def chat_js():
    with open(CHAT_JS, encoding='utf-8') as handle:
        return handle.read()


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as test_client:
        yield test_client


def test_mp4_is_an_accepted_upload_type():
    """The container both engines can record and play has to survive the
    server's allowlist, or negotiating it client-side achieves nothing."""
    from app.routes import ALLOWED_AUDIO_MIME_TYPES
    assert 'audio/mp4' in ALLOWED_AUDIO_MIME_TYPES


def test_the_recorder_negotiates_a_container(chat_js):
    assert 'pickRecorderMime' in chat_js
    assert 'MediaRecorder.isTypeSupported' in chat_js


def test_mp4_is_preferred_over_webm(chat_js):
    """Order is the whole point: MP4/AAC is the only container both engines
    record and play, so it has to be tried before WebM."""
    block = chat_js.split('function pickRecorderMime')[1].split('}')[0]
    mp4 = block.index('audio/mp4')
    webm = block.index('audio/webm')
    assert mp4 < webm, 'WebM would be chosen first on Chrome, which iOS may not play'


def test_playback_checks_the_browser_can_decode(chat_js):
    """canPlayType returning '' is the only signal available before the
    element silently plays nothing."""
    assert 'canPlayType(mime)' in chat_js
    assert 'Download instead' in chat_js


def test_recording_can_be_discarded(chat_js, client):
    """Without this the only way to stop recording also sends it."""
    assert 'function cancelVoiceRecording' in chat_js
    page = client.get('/').get_data(as_text=True)
    assert 'id="voiceCancelBtn"' in page


def test_the_recording_clock_is_shown(chat_js, client):
    """The recorder stops itself at MAX_VOICE_SECONDS; a cap the user cannot
    see is a cap that cuts them off mid-sentence."""
    assert 'formatVoiceClock' in chat_js
    page = client.get('/').get_data(as_text=True)
    assert 'id="voiceTimer"' in page
