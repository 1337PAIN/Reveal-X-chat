"""Protocol v4 on the server: v, msg_uuid, sent_at and epk are stored,
msg_uuid is unique per conversation, and new encrypted messages must be v4.

The cryptography is in the browser and is tested in tests/js/e2ee.test.mjs.
What the server owns is the envelope: it has to carry `v`, `msg_uuid` and
`sent_at` and `epk` back out unchanged, because the recipient rebuilds the
AES-GCM associated data and the per-message key from them, and it refuses a msg_uuid the conversation has
already seen so a client cannot be made to store the same ciphertext twice.

sent_at must be an integer within five minutes of the server's clock, and epk
a P-256 public key. Both are sanity checks only: the server is not trusted,
and the client's own checks are the security boundary.
"""

import base64
import threading
import time
import uuid

import pytest

from app import app as flask_app, socketio
from e2ee_payload import epk, now_ms

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


@pytest.fixture
def pair(store):
    _received.clear()
    store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')
    store.create_user('carol', 'correct-horse-3')
    clients = []
    users = []
    for name, password in (('alice', 'correct-horse-1'), ('bob', 'correct-horse-2')):
        client = socketio.test_client(flask_app)
        client.emit('login', {'username': name, 'password': password})
        users.append(events(client, 'auth_success')[0]['args'][0]['user'])
        clients.append(client)
    for client in clients:
        client.get_received()
    _received.clear()
    yield clients[0], clients[1], users[0], users[1]
    for client in clients:
        client.disconnect()


def text_payload(recipient_id, msg_uuid=None, **overrides):
    payload = {
        'recipient_id': recipient_id,
        'message': 'Y2lwaGVydGV4dA==',
        'iv': 'MTIzNDU2Nzg5MDEy',
        'encrypted': True,
        'v': 4,
        'msg_uuid': msg_uuid or str(uuid.uuid4()),
        'sent_at': now_ms(),
        'epk': epk(),
    }
    payload.update(overrides)
    return payload


def voice_payload(recipient_id, msg_uuid=None, **overrides):
    payload = {
        'recipient_id': recipient_id,
        'audio': base64.b64encode(b'\x01' * 2048).decode('ascii'),
        'iv': 'MTIzNDU2Nzg5MDEy',
        'mime': 'audio/webm',
        'encrypted': True,
        'v': 4,
        'msg_uuid': msg_uuid or str(uuid.uuid4()),
        'sent_at': now_ms(),
        'epk': epk(),
    }
    payload.update(overrides)
    return payload


def errors(client):
    return [e['args'][0]['error'] for e in events(client, 'message_error')]


@pytest.mark.parametrize('event,build', [('send_message', text_payload),
                                         ('send_voice', voice_payload)])
def test_v4_envelope_is_stored_and_handed_back(pair, store, event, build):
    alice_client, bob_client, alice, bob = pair
    msg_uuid = str(uuid.uuid4())
    payload = build(bob['id'], msg_uuid)
    alice_client.emit(event, payload)

    delivered = events(bob_client, 'new_message')[0]['args'][0]
    assert delivered['extra']['v'] == 4
    assert delivered['extra']['msg_uuid'] == msg_uuid
    assert delivered['extra']['sent_at'] == payload['sent_at']
    assert delivered['extra']['epk'] == payload['epk']

    # And from history, which is where most messages are decrypted from.
    history = store.get_conversation(bob['id'], alice['id'])
    assert history[-1]['extra']['msg_uuid'] == msg_uuid


@pytest.mark.parametrize('event,build', [('send_message', text_payload),
                                         ('send_voice', voice_payload)])
def test_a_repeated_msg_uuid_is_refused(pair, store, event, build):
    alice_client, bob_client, alice, bob = pair
    msg_uuid = str(uuid.uuid4())
    alice_client.emit(event, build(bob['id'], msg_uuid))
    alice_client.emit(event, build(bob['id'], msg_uuid))

    assert len(events(bob_client, 'new_message')) == 1
    assert any('already' in e.lower() for e in errors(alice_client))
    assert len(store.get_conversation(alice['id'], bob['id'])) == 1


def test_the_uuid_is_unique_across_both_directions(pair, store):
    """Per conversation, not per sender: Bob cannot re-send Alice's uuid."""
    alice_client, bob_client, alice, bob = pair
    msg_uuid = str(uuid.uuid4())
    alice_client.emit('send_message', text_payload(bob['id'], msg_uuid))
    bob_client.emit('send_message', text_payload(alice['id'], msg_uuid))
    assert len(store.get_conversation(alice['id'], bob['id'])) == 1
    assert errors(bob_client)


def test_the_same_uuid_in_another_conversation_is_independent(pair, store):
    alice_client, bob_client, alice, bob = pair
    carol = next(u for u in store.get_users_list(alice['id']) if u['username'] == 'carol')
    msg_uuid = str(uuid.uuid4())
    alice_client.emit('send_message', text_payload(bob['id'], msg_uuid))
    alice_client.emit('send_message', text_payload(carol['id'], msg_uuid))
    assert not errors(alice_client)


@pytest.mark.parametrize('overrides,why', [
    ({'v': None}, 'no version'),
    ({'v': 1}, 'v1 is retired'),
    ({'v': 2}, 'v2 is retired'),
    ({'v': 3}, 'v3 is retired'),
    ({'epk': None}, 'no ephemeral key'),
    ({'epk': 'not base64!'}, 'epk not base64'),
    ({'epk': 'QUJD'}, 'epk far too short'),
    ({'epk': 'wrong-curve'}, 'epk on another curve'),
    ({'epk': 'off-curve'}, 'epk not a point on P-256'),
    ({'epk': 12345}, 'epk not a string'),
    ({'v': True}, 'a bool is not a version'),
    ({'v': '3'}, 'a string is not a version'),
    ({'sent_at': None}, 'no sent_at'),
    ({'sent_at': 'now'}, 'sent_at not a number'),
    ({'sent_at': 1.5e12}, 'sent_at not an integer'),
    ({'sent_at': True}, 'a bool is not a time'),
    ({'sent_at': 'skew-past'}, 'sent_at six minutes behind the server'),
    ({'sent_at': 'skew-future'}, 'sent_at six minutes ahead of the server'),
    ({'msg_uuid': None}, 'no uuid'),
    ({'msg_uuid': 'not-a-uuid'}, 'malformed uuid'),
    ({'msg_uuid': 'x' * 400}, 'oversized uuid'),
])
def test_new_encrypted_messages_must_be_well_formed_v4(pair, store, overrides, why):
    alice_client, bob_client, alice, bob = pair
    payload = text_payload(bob['id'])
    skews = {'skew-past': -6 * 60 * 1000, 'skew-future': 6 * 60 * 1000}
    for key, value in overrides.items():
        if value is None:
            payload.pop(key)
        elif value in skews:
            payload[key] = now_ms() + skews[value]
        elif value in ('wrong-curve', 'off-curve'):
            payload[key] = _bad_epk(value)
        else:
            payload[key] = value
    alice_client.emit('send_message', payload)
    assert not events(bob_client, 'new_message'), why
    assert errors(alice_client), why


def test_plaintext_messages_need_no_envelope(pair):
    """Plaintext has no AAD to bind, so nothing changes for it here."""
    alice_client, bob_client, alice, bob = pair
    alice_client.emit('send_message', {'recipient_id': bob['id'], 'message': 'hello'})
    assert events(bob_client, 'new_message')


def test_the_database_enforces_uniqueness_under_a_race(store, users):
    """The handler's check and the insert are separate steps; the unique index
    is what makes two simultaneous sends of one uuid store exactly one row."""
    alice, bob = users
    msg_uuid = str(uuid.uuid4())
    barrier = threading.Barrier(6)
    results = []

    def send():
        barrier.wait()
        results.append(store.add_message(
            alice['id'], bob['id'], 'text', 'Y2lwaGVy',
            {'encrypted': True, 'iv': 'aXY=', 'v': 4, 'msg_uuid': msg_uuid, 'sent_at': now_ms(),
             'epk': epk()}))

    threads = [threading.Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for r in results if r) == 1
    assert len(store.get_conversation(alice['id'], bob['id'])) == 1


def test_chat_js_sends_the_envelope_and_opens_through_the_full_check():
    """The browser half. Every read goes through openMessage -- decrypt, then
    freshness, then the persistent replay check -- with the row's context;
    a bare decrypt would skip the last two. All three send paths forward v,
    msg_uuid, sent_at and epk."""
    import re
    from chat_source import chat_source
    chat = chat_source()
    assert 'revealxE2EE.decrypt' not in chat, 'a read bypasses openMessage'
    opens = re.findall(r'revealxE2EE\.openMessage\(([^;]*?)\);', chat, re.S)
    assert len(opens) == 1 and 'e2eeContext(msg)' in opens[0], opens
    # One each for send_message, send_voice and send_share.
    assert chat.count('msg_uuid: sealed.msg_uuid') == 3
    assert chat.count('v: sealed.v') == 3
    assert chat.count('sent_at: sealed.sent_at') == 3
    assert chat.count('epk: sealed.epk') == 3


@pytest.mark.parametrize('offset_ms', [-4 * 60 * 1000, 4 * 60 * 1000])
def test_sent_at_within_five_minutes_is_accepted(pair, store, offset_ms):
    """Clocks differ; the window allows for it in both directions."""
    alice_client, bob_client, alice, bob = pair
    alice_client.emit('send_message', text_payload(bob['id'], sent_at=now_ms() + offset_ms))
    assert events(bob_client, 'new_message')
    assert not errors(alice_client)


def test_the_share_envelope_is_v4_too(pair, store):
    from share_helpers import share_payload
    alice_client, bob_client, alice, bob = pair
    payload = share_payload(bob['id'])
    alice_client.emit('send_share', payload)
    delivered = events(bob_client, 'new_message')[0]['args'][0]
    assert delivered['extra']['v'] == 4
    assert delivered['extra']['sent_at'] == payload['sent_at']
    assert delivered['extra']['epk'] == payload['epk']
    stale = share_payload(bob['id'])
    stale['v'] = 3
    alice_client.emit('send_share', stale)
    assert len(events(bob_client, 'new_message')) == 1


def _bad_epk(kind):
    """A well-formed SPKI that is still not acceptable."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if kind == 'wrong-curve':
        key = ec.generate_private_key(ec.SECP384R1()).public_key()
        der = key.public_bytes(serialization.Encoding.DER,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
    else:
        good = base64.b64decode(epk())
        # Same P-256 header, a point that is not on the curve.
        der = good[:27] + bytes([1] * 64)
    return base64.b64encode(der).decode('ascii')


def test_the_ephemeral_key_is_never_the_senders_long_term_one(pair, store):
    """Sending one's static key as the epk would quietly turn v4 back into a
    static-static scheme with no forward secrecy. Refused by name."""
    alice_client, bob_client, alice, bob = pair
    store.set_public_key(alice['id'], epk_value := epk())
    alice_client.emit('send_message', text_payload(bob['id'], epk=epk_value))
    assert not events(bob_client, 'new_message')
    assert errors(alice_client)
