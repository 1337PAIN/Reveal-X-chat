"""Group conversations are off unless REVEALX_GROUPS turns them on.

Groups exist only on the server: there is no client for them. An event
handler nothing legitimate calls is still attack surface, so in a default
deployment every group_* event is refused before it touches the database.
tests/test_groups.py turns the flag on to test the feature itself.
"""

import pytest

from app import app as flask_app, socketio
from app.handlers import groups

GROUP_EVENTS = [
    ('group_create', {'name': 'team', 'member_ids': []}),
    ('group_list', None),
    ('group_open', {'group_id': 'g'}),
    ('group_send', {'group_id': 'g', 'payloads': {}}),
    ('group_add_members', {'group_id': 'g', 'member_ids': []}),
    ('group_remove_member', {'group_id': 'g', 'user_id': 'u'}),
    ('group_set_role', {'group_id': 'g', 'user_id': 'u', 'role': 'admin'}),
    ('group_update_info', {'group_id': 'g', 'name': 'x'}),
    ('group_update_settings', {'group_id': 'g'}),
    ('group_leave', {'group_id': 'g'}),
]


def test_every_group_handler_is_listed_here():
    registered = {e for e in socketio.server.handlers['/'] if e.startswith('group_')}
    assert registered == {name for name, _ in GROUP_EVENTS}


@pytest.fixture
def alice_socket(store):
    store.create_user('alice', 'correct-horse-1')
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    sock.get_received()
    yield sock
    sock.disconnect()


@pytest.mark.parametrize('value', [None, '', 'false', 'off', '0', 'no'])
def test_groups_are_off_unless_explicitly_enabled(monkeypatch, value):
    if value is None:
        monkeypatch.delenv('REVEALX_GROUPS', raising=False)
    else:
        monkeypatch.setenv('REVEALX_GROUPS', value)
    assert groups.groups_enabled() is False


@pytest.mark.parametrize('value', ['true', 'TRUE', '1', 'on', 'yes'])
def test_groups_turn_on_with_the_flag(monkeypatch, value):
    monkeypatch.setenv('REVEALX_GROUPS', value)
    assert groups.groups_enabled() is True


@pytest.mark.parametrize('name,payload', GROUP_EVENTS, ids=[n for n, _ in GROUP_EVENTS])
def test_with_groups_off_every_event_is_refused_and_creates_nothing(
        monkeypatch, store, alice_socket, name, payload):
    monkeypatch.delenv('REVEALX_GROUPS', raising=False)
    if payload is None:
        alice_socket.emit(name)
    else:
        alice_socket.emit(name, payload)
    received = alice_socket.get_received()
    errors = [r['args'][0]['error'] for r in received if r['name'] == 'group_error']
    assert errors == ['Group conversations are not enabled on this server']
    assert [r['name'] for r in received] == ['group_error']
    with store._connect() as db:
        assert db.execute('SELECT COUNT(*) AS n FROM groups').fetchone()['n'] == 0


def test_with_groups_on_the_same_event_works(monkeypatch, store, alice_socket):
    monkeypatch.setenv('REVEALX_GROUPS', 'true')
    alice_socket.emit('group_create', {'name': 'team', 'member_ids': []})
    names = [r['name'] for r in alice_socket.get_received()]
    assert 'group_created' in names and 'group_error' not in names
