"""Versioned migrations, and upgrades from databases made before them.

The schema used to be rebuilt on every start by one function with no record
of what a database's version was. These check that the numbered steps produce
the schema the code expects, that they run once, and -- the part that cannot
be allowed to regress -- that a database created by an older Reveal-X, with
no schema_version table at all, upgrades with its data intact.
"""

import sqlite3
from contextlib import closing

import pytest

from app import migrations
from app.models import ChatRoom

EXPECTED = {
    'users': {'id', 'username', 'password_hash', 'created_at', 'last_seen', 'profile_image',
              'public_key', 'bio', 'status', 'firebase_uid', 'email', 'auth_provider',
              'totp_secret', 'role', 'account_state', 'session_epoch'},
    'messages': {'id', 'sender_id', 'recipient_id', 'type', 'content', 'extra', 'created_at',
                 'expires_at', 'read_at', 'share1_accessed', 'deleted_by', 'group_id',
                 'group_message_id', 'msg_uuid_key'},
    'groups': {'id', 'name', 'description', 'created_by', 'created_at', 'only_admins_post',
               'only_admins_edit_info'},
    'group_members': {'group_id', 'user_id', 'role', 'joined_at'},
    'resume_tokens': {'token_hash', 'user_id', 'session_epoch', 'expires_at', 'created_at'},
}


def columns(path, table):
    with closing(sqlite3.connect(path)) as conn, conn:
        return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def versions(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        return [row[0] for row in conn.execute('SELECT version FROM schema_version ORDER BY version')]


def open_store(tmp_path, name='db.sqlite'):
    path = tmp_path / name
    shares = tmp_path / 'shares'
    shares.mkdir(exist_ok=True)
    return ChatRoom(db_path=str(path), shares_folder=str(shares)), path


def test_the_steps_are_numbered_once_and_in_order():
    numbers = [m[0] for m in migrations.MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1))
    assert migrations.LATEST_VERSION == numbers[-1]


def test_a_fresh_database_gets_every_column_and_every_version(tmp_path):
    store, path = open_store(tmp_path)
    for table, expected in EXPECTED.items():
        assert expected <= columns(path, table), (table, expected - columns(path, table))
    assert versions(path) == [m[0] for m in migrations.MIGRATIONS]


def test_starting_again_applies_nothing(tmp_path):
    store, path = open_store(tmp_path)
    assert migrations.migrate(store) == []
    store.init_db()
    assert versions(path) == [m[0] for m in migrations.MIGRATIONS]


def test_a_pre_versioning_database_of_the_oldest_shape_upgrades_with_its_data(tmp_path):
    path = tmp_path / 'old.sqlite'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, "
                     "password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, sender_id TEXT NOT NULL, "
                     "recipient_id TEXT NOT NULL, type TEXT NOT NULL, content TEXT NOT NULL, "
                     "extra TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO users VALUES ('u1', 'veteran', 'x', '2025-01-01T00:00:00')")
        conn.execute("INSERT INTO messages VALUES ('m1', 'u1', 'u1', 'text', 'kept', '{}', "
                     "'2025-01-01T00:00:00')")

    shares = tmp_path / 'shares'
    shares.mkdir()
    ChatRoom(db_path=str(path), shares_folder=str(shares))

    for table, expected in EXPECTED.items():
        assert expected <= columns(path, table), table
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("SELECT username, account_state, role FROM users").fetchone() == \
            ('veteran', 'active', 'user'), 'an old account must not be locked out'
        assert conn.execute("SELECT content FROM messages").fetchone() == ('kept',)
    assert versions(path)[-1] == migrations.LATEST_VERSION


def test_a_current_database_from_before_this_file_upgrades_cleanly(tmp_path):
    """Built by the old init_db: every column present, no schema_version."""
    store, path = open_store(tmp_path)
    user, _ = store.create_user('existing', 'correct-horse-1')
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DROP TABLE schema_version')

    store.init_db()
    assert versions(path) == [m[0] for m in migrations.MIGRATIONS]
    assert store.get_user(user['id'])['username'] == 'existing'


def test_a_failed_step_is_not_recorded(tmp_path, monkeypatch):
    store, path = open_store(tmp_path)

    def broken(room, db):
        raise RuntimeError('half-way')

    monkeypatch.setattr(migrations, 'MIGRATIONS',
                        migrations.MIGRATIONS + [(migrations.LATEST_VERSION + 1, 'broken', broken)])
    with pytest.raises(RuntimeError):
        migrations.migrate(store)
    assert versions(path)[-1] == migrations.LATEST_VERSION
