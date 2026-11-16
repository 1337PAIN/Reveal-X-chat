"""The chat menu: pin, archive, mute and clear, per person and per chat.

The ⋮ beside a contact's name holds what you can do with that conversation.
Pin, archive and mute are your own view of it: stored on the server per
account and per contact (migration 13), so they follow you to another
device, and sent with the roster. Clear chat hides every message in the
conversation from you only; once both people have cleared a message it is
deleted outright, with its stored share.
"""

import pytest

from app import app as flask_app, socketio

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def roster(client):
    return {u['username']: u for u in events(client, 'users_updated')[-1]['args'][0]['users']}


@pytest.fixture
def world(store):
    _received.clear()
    alice, _ = store.create_user('alice', 'correct-horse-1')
    bob, _ = store.create_user('bob', 'correct-horse-2')
    a = socketio.test_client(flask_app)
    a.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    b = socketio.test_client(flask_app)
    b.emit('login', {'username': 'bob', 'password': 'correct-horse-2'})
    yield a, b, alice, bob
    a.disconnect()
    b.disconnect()


def test_a_contact_starts_with_nothing_set(world, store):
    a, b, alice, bob = world
    entry = store.get_users_list(alice['id'])[0]
    assert (entry['pinned'], entry['archived'], entry['muted']) == (False, False, False)


@pytest.mark.parametrize('pref', ['pinned', 'archived', 'muted'])
def test_each_setting_is_saved_and_sent_with_the_roster(world, store, pref):
    a, b, alice, bob = world
    a.emit('chat_pref', {'peer_id': bob['id'], 'pref': pref, 'value': True})
    assert roster(a)['bob'][pref] is True
    assert store.get_users_list(alice['id'])[0][pref] is True
    a.emit('chat_pref', {'peer_id': bob['id'], 'pref': pref, 'value': False})
    assert roster(a)['bob'][pref] is False


def test_settings_are_yours_alone(world, store):
    a, b, alice, bob = world
    a.emit('chat_pref', {'peer_id': bob['id'], 'pref': 'muted', 'value': True})
    assert store.get_users_list(bob['id'])[0]['muted'] is False


def test_the_latest_pin_sorts_first(world, store):
    a, b, alice, bob = world
    carol, _ = store.create_user('carol', 'correct-horse-3')
    a.emit('chat_pref', {'peer_id': bob['id'], 'pref': 'pinned', 'value': True})
    a.emit('chat_pref', {'peer_id': carol['id'], 'pref': 'pinned', 'value': True})
    listed = {u['username']: u for u in store.get_users_list(alice['id'])}
    assert listed['carol']['pinned_at'] >= listed['bob']['pinned_at']


def test_unknown_settings_and_strangers_are_ignored(world, store):
    a, b, alice, bob = world
    stranger, _ = store.create_user('dave', 'correct-horse-4')
    store.remove_contact(alice['id'], stranger['id'])
    a.emit('chat_pref', {'peer_id': bob['id'], 'pref': 'is_admin', 'value': True})
    a.emit('chat_pref', {'peer_id': stranger['id'], 'pref': 'pinned', 'value': True})
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM chat_prefs").fetchone()['n'] == 0


def test_clearing_a_chat_hides_it_from_you_only(world, store):
    a, b, alice, bob = world
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'one'})
    b.emit('send_message', {'recipient_id': alice['id'], 'message': 'two'})
    a.emit('clear_chat', {'peer_id': bob['id']})
    assert store.get_conversation(alice['id'], bob['id']) == []
    assert len(store.get_conversation(bob['id'], alice['id'])) == 2
    assert events(a, 'chat_cleared')[-1]['args'][0] == {'peer_id': bob['id']}


def test_a_message_both_have_cleared_is_deleted(world, store):
    a, b, alice, bob = world
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'one'})
    a.emit('clear_chat', {'peer_id': bob['id']})
    b.emit('clear_chat', {'peer_id': alice['id']})
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM messages").fetchone()['n'] == 0


def test_deleting_an_account_removes_its_settings(world, store):
    a, b, alice, bob = world
    a.emit('chat_pref', {'peer_id': bob['id'], 'pref': 'pinned', 'value': True})
    b.emit('chat_pref', {'peer_id': alice['id'], 'pref': 'muted', 'value': True})
    store.purge_user(alice['id'])
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM chat_prefs").fetchone()['n'] == 0
