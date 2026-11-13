"""Protocol v2 on the server: msg_uuid is stored, unique per conversation, and
new encrypted messages must be v2.

The cryptography is in the browser and is tested in tests/js/e2ee.test.mjs.
What the server owns is the envelope: it has to carry `v` and `msg_uuid` back
out unchanged, because the recipient rebuilds the AES-GCM associated data from
them, and it refuses a msg_uuid the conversation has already seen so a client
cannot be made to store the same ciphertext twice.
"""

import base64
import threading
import uuid

import pytest

from app import app as flask_app, socketio

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
        'v': 2,
        'msg_uuid': msg_uuid or str(uuid.uuid4()),
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
        'v': 2,
        'msg_uuid': msg_uuid or str(uuid.uuid4()),
    }
    payload.update(overrides)
    return payload


def errors(client):
    return [e['args'][0]['error'] for e in events(client, 'message_error')]


@pytest.mark.parametrize('event,build', [('send_message', text_payload),
                                         ('send_voice', voice_payload)])
def test_v2_envelope_is_stored_and_handed_back(pair, store, event, build):
    alice_client, bob_client, alice, bob = pair
    msg_uuid = str(uuid.uuid4())
    alice_client.emit(event, build(bob['id'], msg_uuid))

    delivered = events(bob_client, 'new_message')[0]['args'][0]
    assert delivered['extra']['v'] == 2
    assert delivered['extra']['msg_uuid'] == msg_uuid

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
    ({'v': 1}, 'v1 is decrypt-only now'),
    ({'msg_uuid': None}, 'no uuid'),
    ({'msg_uuid': 'not-a-uuid'}, 'malformed uuid'),
    ({'msg_uuid': 'x' * 400}, 'oversized uuid'),
])
def test_new_encrypted_messages_must_be_well_formed_v2(pair, store, overrides, why):
    alice_client, bob_client, alice, bob = pair
    payload = text_payload(bob['id'])
    for key, value in overrides.items():
        if value is None:
            payload.pop(key)
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
            {'encrypted': True, 'iv': 'aXY=', 'v': 2, 'msg_uuid': msg_uuid}))

    threads = [threading.Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for r in results if r) == 1
    assert len(store.get_conversation(alice['id'], bob['id'])) == 1


def test_chat_js_sends_the_envelope_and_decrypts_with_it():
    """The browser half: every decrypt call passes the row's context and both
    send paths forward v and msg_uuid. Without the context a v2 row is tried
    as v1 and fails closed, which looks like a broken key, not a bug."""
    import re
    from pathlib import Path
    from chat_source import chat_source
    chat = chat_source()
    decrypts = re.findall(r'revealxE2EE\.decrypt(?:Bytes)?\(([^;]*?)\)\s*(?:\n|:|;)', chat, re.S)
    assert decrypts, 'no decrypt calls found'
    for call in decrypts:
        assert 'e2eeContext(msg)' in call, f'decrypt without context: {call!r}'
    assert len(decrypts) == chat.count('revealxE2EE.decrypt'), 'a decrypt call was missed'
    # One each for send_message, send_voice and send_share.
    assert chat.count('msg_uuid: sealed.msg_uuid') == 3
    assert chat.count('v: sealed.v') == 3
