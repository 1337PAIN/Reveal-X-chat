"""Replying to a specific message.

A reply names the message it answers, and the server attaches a short preview
of it. The preview is the risk: it is built from a message the *sender* can
see and delivered to the *recipient*, so the server must only accept a
message from the same conversation. It used to accept any message the sender
had taken part in, which let Alice show Carol the sender name, type and (for a
message that was not end-to-end encrypted) the first 120 characters of
something Bob had written to her.
"""

import pytest

from app import app as flask_app, socketio

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def sign_in(username, password):
    client = socketio.test_client(flask_app)
    client.emit('login', {'username': username, 'password': password})
    user = events(client, 'auth_success')[0]['args'][0]['user']
    client.get_received()
    return client, user


@pytest.fixture
def trio(store):
    _received.clear()
    store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')
    store.create_user('carol', 'correct-horse-3')
    clients = [sign_in('alice', 'correct-horse-1'), sign_in('bob', 'correct-horse-2'),
               sign_in('carol', 'correct-horse-3')]
    yield clients
    for client, _ in clients:
        client.disconnect()


def test_a_reply_carries_a_preview_of_the_message_it_answers(trio):
    (a, alice), (b, bob), _ = trio
    b.emit('send_message', {'recipient_id': alice['id'], 'message': 'lunch at one?'})
    original = events(a, 'new_message')[-1]['args'][0]

    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'yes', 'reply_to': original['id']})
    reply = events(b, 'new_message')[-1]['args'][0]
    assert reply['content'] == 'yes'
    assert reply['extra']['reply_to']['id'] == original['id']
    assert reply['extra']['reply_to']['content'] == 'lunch at one?'


def test_a_reply_cannot_quote_a_message_from_another_conversation(trio, store):
    (a, alice), (b, bob), (c, carol) = trio
    b.emit('send_message', {'recipient_id': alice['id'], 'message': 'private to alice'})
    from_bob = events(a, 'new_message')[-1]['args'][0]

    a.emit('send_message', {'recipient_id': carol['id'], 'message': 'look',
                            'reply_to': from_bob['id']})

    assert events(a, 'message_error'), 'the reply should be refused'
    assert not events(c, 'new_message'), 'nothing may reach Carol'
    with store._connect() as db:
        stored = [row['content'] for row in db.execute('SELECT content FROM messages')]
    assert 'look' not in stored


def test_a_reply_to_a_message_that_does_not_exist_is_refused(trio):
    (a, alice), (b, bob), _ = trio
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'hm',
                            'reply_to': 'no-such-message'})
    assert events(a, 'message_error')
    assert not events(b, 'new_message')


def test_a_voice_reply_is_held_to_the_same_rule(trio):
    (a, alice), (b, bob), (c, carol) = trio
    b.emit('send_message', {'recipient_id': alice['id'], 'message': 'private to alice'})
    from_bob = events(a, 'new_message')[-1]['args'][0]

    a.emit('send_voice', {'recipient_id': carol['id'],
                          'audio': 'data:audio/webm;base64,' + 'AQID' * 512,
                          'reply_to': from_bob['id']})
    assert events(a, 'message_error')
    assert not events(c, 'new_message')
