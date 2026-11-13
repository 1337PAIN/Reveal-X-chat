"""Reactions over Socket.IO.

This file used to hold the tests for send_image, the server-side split. That
handler is retired: the browser splits the image now, and its claims --
Share 1 XOR Share 2 is the original bit for bit, neither share alone reveals
it, a fresh mask every time -- are held where the split happens, in
tests/js/vc-client.test.mjs, with the server half in tests/test_client_split.py.
"""

import pytest

from app import app as flask_app, socketio

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


@pytest.fixture
def pair(store):
    """Two signed-in sockets."""
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
