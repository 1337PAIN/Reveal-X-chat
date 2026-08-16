"""Storage layer: one-time share access, deletion, retention, and SQL portability."""

import json
import os
from datetime import datetime, timedelta

import pytest

from app.models import FORBIDDEN_EXTRA_KEYS, ChatRoom, _Db


def make_share(store, sender, recipient, token='tok-abc'):
    """Create a share message backed by a real file in the share folder."""
    filename = 'share1_test.png'
    path = os.path.join(store.shares_folder, filename)
    with open(path, 'wb') as handle:
        handle.write(b'not-a-real-png-but-a-real-file')

    message = store.add_message(
        sender['id'], recipient['id'], 'share', 'Shared an encrypted image',
        {'share1_filename': filename, 'share1_token': token, 'share1_hmac': 'deadbeef'},
    )
    return message, path


# ----------------------------------------------------------------------
# One-time share access
# ----------------------------------------------------------------------

def test_share_token_grants_access(store, users):
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    found = store.get_share_message_by_token(message['id'], 'tok-abc')
    assert found is not None
    assert found['id'] == message['id']


def test_wrong_or_missing_token_is_rejected(store, users):
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    assert store.get_share_message_by_token(message['id'], 'wrong-token') is None
    assert store.get_share_message_by_token(message['id'], '') is None


def test_access_is_one_time_once_consumed(store, users):
    """The consuming read must persist, so a second fetch is refused."""
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    assert store.get_share_message_by_token(message['id'], 'tok-abc', consume=True) is not None
    assert store.get_share_message_by_token(message['id'], 'tok-abc', consume=True) is None
    assert store.get_share_message_by_token(message['id'], 'tok-abc') is None


def test_inspection_without_consume_does_not_burn_access(store, users):
    """The integrity check runs before reconstruction and must not use up the share."""
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    for _ in range(3):
        assert store.get_share_message_by_token(message['id'], 'tok-abc', consume=False) is not None

    assert store.get_share_message_by_token(message['id'], 'tok-abc', consume=True) is not None


def test_reading_a_message_never_consumes_share_access(store, users):
    """Replies, reactions and forwards all read messages; none may burn a share."""
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    store.get_message_for_user(message['id'], bob['id'])
    store.get_message_for_user(message['id'], alice['id'])
    store.get_conversation(alice['id'], bob['id'])

    assert store.get_share_message_by_token(message['id'], 'tok-abc', consume=True) is not None


def test_revoking_blocks_further_access(store, users):
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    store.revoke_share_access(message['id'])
    assert store.get_share_message_by_token(message['id'], 'tok-abc') is None


def test_expired_share_is_not_served(store, users):
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    past = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    with store._connect() as db:
        db.execute("UPDATE messages SET expires_at = ? WHERE id = ?", (past, message['id']))

    assert store.get_share_message_by_token(message['id'], 'tok-abc') is None


# ----------------------------------------------------------------------
# Deletion
# ----------------------------------------------------------------------

def test_recipient_delete_only_hides_their_own_copy(store, users):
    """The recipient can delete for themselves; the sender keeps theirs."""
    alice, bob = users
    message = store.add_message(alice['id'], bob['id'], 'text', 'hello', {})

    scope, notify = store.delete_message(message['id'], bob['id'])

    assert scope == 'self'
    assert notify == (bob['id'],)

    assert store.get_message_for_user(message['id'], bob['id']) is None
    assert store.get_conversation(bob['id'], alice['id']) == []

    assert store.get_message_for_user(message['id'], alice['id']) is not None
    assert len(store.get_conversation(alice['id'], bob['id'])) == 1


def test_sender_delete_removes_it_for_everyone(store, users):
    """The sender owns what they sent, so their delete clears both sides."""
    alice, bob = users
    message, path = make_share(store, alice, bob)

    scope, notify = store.delete_message(message['id'], alice['id'])

    assert scope == 'everyone'
    assert set(notify) == {alice['id'], bob['id']}

    assert not os.path.isfile(path)
    assert store.get_message_for_user(message['id'], alice['id']) is None
    assert store.get_message_for_user(message['id'], bob['id']) is None
    assert store.get_conversation(alice['id'], bob['id']) == []


def test_recipient_delete_leaves_the_share_file_for_the_sender(store, users):
    alice, bob = users
    message, path = make_share(store, alice, bob)

    store.delete_message(message['id'], bob['id'])

    assert os.path.isfile(path)
    assert len(store.get_conversation(alice['id'], bob['id'])) == 1

    # The sender deleting afterwards still clears everything.
    store.delete_message(message['id'], alice['id'])
    assert not os.path.isfile(path)


def test_sender_can_delete_after_the_recipient_already_did(store, users):
    alice, bob = users
    message = store.add_message(alice['id'], bob['id'], 'text', 'hello', {})

    store.delete_message(message['id'], bob['id'])
    scope, _ = store.delete_message(message['id'], alice['id'])

    assert scope == 'everyone'
    assert store.get_message_for_user(message['id'], alice['id']) is None


def test_deleting_a_share_gives_up_the_recipients_access(store, users):
    """A recipient who deletes the message cannot still fetch its share."""
    alice, bob = users
    message, _ = make_share(store, alice, bob)

    store.delete_message(message['id'], bob['id'])

    assert store.get_share_message_by_token(message['id'], 'tok-abc') is None


def test_recipient_deleting_twice_is_harmless(store, users):
    alice, bob = users
    message = store.add_message(alice['id'], bob['id'], 'text', 'hello', {})

    assert store.delete_message(message['id'], bob['id'])[0] == 'self'
    assert store.delete_message(message['id'], bob['id'])[0] == 'self'
    # Alice's copy is untouched by bob deleting twice.
    assert store.get_message_for_user(message['id'], alice['id']) is not None


def test_non_participant_cannot_delete(store, users):
    alice, bob = users
    carol, error = store.create_user('carol', 'correct-horse-3')
    assert error is None

    message = store.add_message(alice['id'], bob['id'], 'text', 'private', {})
    assert store.delete_message(message['id'], carol['id']) is None
    assert store.get_message_for_user(message['id'], alice['id']) is not None
    assert store.get_message_for_user(message['id'], bob['id']) is not None


def test_deleting_an_account_clears_its_messages_and_files(store, users):
    alice, bob = users
    _, path = make_share(store, alice, bob)

    store.delete_user_messages(alice['id'])
    store.delete_user(alice['id'])

    assert not os.path.isfile(path)
    assert store.get_user(alice['id']) is None


# ----------------------------------------------------------------------
# Share 2 must never be persisted
# ----------------------------------------------------------------------

@pytest.mark.parametrize('key', sorted(FORBIDDEN_EXTRA_KEYS))
def test_share2_is_stripped_before_storage(store, users, key):
    alice, bob = users
    message = store.add_message(
        alice['id'], bob['id'], 'share', 'Shared an encrypted image',
        {'share1_token': 'tok', key: 'SECRET-SHARE-2-DATA'},
    )

    stored = store.get_message_for_user(message['id'], bob['id'])
    assert key not in stored['extra']
    assert 'SECRET-SHARE-2-DATA' not in json.dumps(stored['extra'])


def test_startup_purges_legacy_stored_share2(store, users):
    alice, bob = users
    message = store.add_message(alice['id'], bob['id'], 'share', 'legacy', {'share1_token': 'tok'})

    # Simulate a row written by an older version that kept Share 2.
    with store._connect() as db:
        db.execute(
            "UPDATE messages SET extra = ? WHERE id = ?",
            (json.dumps({'share1_token': 'tok', 'share2_b64': 'LEAK'}), message['id']),
        )

    store.init_db()

    stored = store.get_message_for_user(message['id'], bob['id'])
    assert 'share2_b64' not in stored['extra']


# ----------------------------------------------------------------------
# Encryption metadata and retention
# ----------------------------------------------------------------------

def test_encrypted_flag_reflects_the_payload(store, users):
    alice, bob = users
    plain = store.add_message(alice['id'], bob['id'], 'text', 'hello', {})
    sealed = store.add_message(alice['id'], bob['id'], 'text', 'Y2lwaGVy', {'encrypted': True, 'iv': 'aXY='})

    assert plain['encrypted'] is False
    assert plain['is_legacy'] is True
    assert sealed['encrypted'] is True
    assert sealed['is_legacy'] is False

    reloaded = store.get_message_for_user(sealed['id'], bob['id'])
    assert reloaded['encrypted'] is True
    assert reloaded['extra']['iv'] == 'aXY='


def test_public_key_round_trip(store, users):
    alice, _ = users
    store.set_public_key(alice['id'], 'BASE64PUBLICKEY==')
    assert store.get_user(alice['id'])['public_key'] == 'BASE64PUBLICKEY=='


def test_cleanup_deletes_expired_messages_and_their_files(store, users):
    alice, bob = users
    message, path = make_share(store, alice, bob)

    stale = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    with store._connect() as db:
        db.execute("UPDATE messages SET created_at = ?, expires_at = ? WHERE id = ?",
                   (stale, stale, message['id']))

    store.cleanup_old_messages()

    assert not os.path.isfile(path)
    assert store.get_conversation(alice['id'], bob['id']) == []


def test_read_receipts_mark_only_incoming_messages(store, users):
    alice, bob = users
    from_alice = store.add_message(alice['id'], bob['id'], 'text', 'hi', {})
    from_bob = store.add_message(bob['id'], alice['id'], 'text', 'hey', {})

    ids, read_at = store.mark_messages_read(bob['id'], alice['id'])

    assert ids == [from_alice['id']]
    assert read_at
    assert store.get_message_for_user(from_bob['id'], bob['id'])['read_at'] is None


# ----------------------------------------------------------------------
# Accounts
# ----------------------------------------------------------------------

def test_duplicate_usernames_are_rejected(store, users):
    _, error = store.create_user('alice', 'another-password-9')
    assert error == 'Username already exists'


@pytest.mark.parametrize('username,password', [
    ('ab', 'correct-horse-1'),        # too short
    ('has space', 'correct-horse-1'),  # invalid characters
    ('validname', 'short'),            # password too short
    ('samename', 'samename'),          # password equals username
])
def test_registration_validation(store, username, password):
    user, error = store.create_user(username, password)
    assert user is None and error


def test_authentication_round_trip(store, users):
    user, error = store.authenticate('alice', 'correct-horse-1')
    assert error is None and user['username'] == 'alice'

    user, error = store.authenticate('alice', 'wrong-password')
    assert user is None
    # The same message for both cases, so it cannot be used to enumerate accounts.
    assert error == 'Invalid username or password'
    assert store.authenticate('nobody', 'wrong-password')[1] == error


# ----------------------------------------------------------------------
# SQL portability
# ----------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params):
        self.log.append((sql, params))


class _FakePgConnection:
    """Stands in for psycopg2, which offers no connection-level execute()."""

    def __init__(self):
        self.statements = []

    def cursor(self):
        return _FakeCursor(self.statements)


def test_postgres_statements_go_through_a_cursor_with_translated_placeholders():
    conn = _FakePgConnection()
    db = _Db(conn, use_postgres=True)

    db.execute("SELECT * FROM messages WHERE id = ? AND type = ?", ('abc', 'share'))

    sql, params = conn.statements[0]
    assert '?' not in sql
    assert sql.endswith('WHERE id = %s AND type = %s')
    assert params == ('abc', 'share')


def test_sqlite_statements_keep_question_marks(store):
    with store._connect() as db:
        rows = db.execute("SELECT id FROM users WHERE username = ?", ('nobody',)).fetchall()
    assert rows == []


def test_connection_is_closed_after_use(store):
    """A leaked connection would keep the sqlite file locked open."""
    conn = store._raw_connection()
    conn.close()

    with store._connect() as db:
        db.execute("SELECT 1")

    # Reconnecting and writing must still work, i.e. nothing is holding a lock.
    with store._connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS probe (id TEXT)")
        db.execute("INSERT INTO probe (id) VALUES (?)", ('x',))
    with store._connect() as db:
        assert len(db.execute("SELECT id FROM probe").fetchall()) == 1


def test_failed_transaction_rolls_back(store, users):
    alice, bob = users
    message = store.add_message(alice['id'], bob['id'], 'text', 'keep me', {})

    with pytest.raises(RuntimeError):
        with store._connect() as db:
            db.execute("DELETE FROM messages WHERE id = ?", (message['id'],))
            raise RuntimeError('boom')

    assert store.get_message_for_user(message['id'], alice['id']) is not None


def test_default_share_folder_is_absolute():
    """Share cleanup must not depend on the process working directory."""
    import app.models as models

    expected = os.path.join(os.path.dirname(os.path.abspath(models.__file__)), 'shares')
    assert os.path.isabs(expected)
    assert models.chat_room.shares_folder == expected


@pytest.mark.parametrize('filename', [
    '../../etc/passwd',
    'sub/dir/share.png',
    '',
    None,
])
def test_share_paths_reject_traversal(store, filename):
    assert store._share_path(filename) is None


def test_share_path_accepts_a_plain_filename(store):
    resolved = store._share_path('share1_ok.png')
    assert resolved == os.path.join(store.shares_folder, 'share1_ok.png')
