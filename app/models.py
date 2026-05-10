"""
SQLite/PostgreSQL-backed accounts, online presence, and private chat storage.
[MODIFIED] Added PostgreSQL support, argon2id, share expiration, one-time access
"""

from datetime import datetime, timedelta
import json
import os
import re
import secrets
import sqlite3
import uuid
from urllib.parse import urlparse

from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg2
    import psycopg2.extras
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    ARGON2_AVAILABLE = True
except ImportError:
    ARGON2_AVAILABLE = False


USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,30}$')


class ChatRoom:
    """Manages persistent users, expiring messages, and online sessions."""

    def __init__(self, db_path=None):
        # [NEW] PostgreSQL support via DATABASE_URL env var
        self.database_url = os.environ.get('DATABASE_URL')
        if self.database_url and POSTGRES_AVAILABLE:
            self.use_postgres = True
            self.db_path = None
        else:
            self.use_postgres = False
            self.db_path = db_path or os.path.join('app', 'data', 'reveal_x.db')

        self.sessions = {}  # {session_id: user dict}
        self.user_sessions = {}  # {user_id: set(session_id)}
        self.init_db()

        # [NEW] Argon2 password hasher
        if ARGON2_AVAILABLE:
            self.password_hasher = PasswordHasher(
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
                hash_len=32,
                salt_len=16
            )
        else:
            self.password_hasher = None

    def _get_connection(self):
        """Get database connection (PostgreSQL or SQLite)."""
        if self.use_postgres:
            conn = psycopg2.connect(self.database_url)
            conn.cursor_factory = psycopg2.extras.RealDictCursor
            return conn
        else:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def init_db(self):
        with self._get_connection() as conn:
            if self.use_postgres:
                # PostgreSQL schema
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        profile_image TEXT,
                        last_seen TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY,
                        sender_id TEXT NOT NULL,
                        recipient_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        extra TEXT NOT NULL DEFAULT '{}',
                        read_at TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        share1_accessed BOOLEAN DEFAULT FALSE,
                        FOREIGN KEY(sender_id) REFERENCES users(id),
                        FOREIGN KEY(recipient_id) REFERENCES users(id)
                    )
                """)
            else:
                # SQLite schema (original)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        profile_image TEXT,
                        last_seen TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY,
                        sender_id TEXT NOT NULL,
                        recipient_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        extra TEXT NOT NULL DEFAULT '{}',
                        read_at TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        share1_accessed BOOLEAN DEFAULT FALSE
                    )
                """)

            # Add columns if they don't exist (for backward compatibility)
            try:
                if self.use_postgres:
                    # Check if columns exist in PostgreSQL
                    existing_columns = conn.execute("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'messages'
                    """).fetchall()
                    existing_columns = [col['column_name'] for col in existing_columns]

                    if 'expires_at' not in existing_columns:
                        conn.execute("ALTER TABLE messages ADD COLUMN expires_at TEXT")
                    if 'share1_accessed' not in existing_columns:
                        conn.execute("ALTER TABLE messages ADD COLUMN share1_accessed BOOLEAN DEFAULT FALSE")
                else:
                    # SQLite - check columns
                    columns = {
                        row['name']
                        for row in conn.execute("PRAGMA table_info(messages)").fetchall()
                    }
                    user_columns = {
                        row['name']
                        for row in conn.execute("PRAGMA table_info(users)").fetchall()
                    }

                    if 'expires_at' not in columns:
                        conn.execute("ALTER TABLE messages ADD COLUMN expires_at TEXT")
                    if 'share1_accessed' not in columns:
                        conn.execute("ALTER TABLE messages ADD COLUMN share1_accessed BOOLEAN DEFAULT FALSE")
                    if 'last_seen' not in user_columns:
                        conn.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
                    if 'profile_image' not in user_columns:
                        conn.execute("ALTER TABLE users ADD COLUMN profile_image TEXT")
                    if 'read_at' not in columns:
                        conn.execute("ALTER TABLE messages ADD COLUMN read_at TEXT")

            except Exception:
                # If we can't check, try to add columns (might fail if already exists, but that's ok)
                pass

            # Create indexes
            if self.use_postgres:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_expires ON messages(expires_at)")
            else:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id)")

            self.purge_stored_share2(conn)

    def _hash_password(self, password):
        """[NEW] Hash password using argon2id or fallback to werkzeug."""
        if ARGON2_AVAILABLE and self.password_hasher:
            return self.password_hasher.hash(password)
        else:
            return generate_password_hash(password)

    def _verify_password(self, password, password_hash):
        """[NEW] Verify password using argon2id or fallback to werkzeug."""
        if not password_hash:
            return False

        if ARGON2_AVAILABLE and self.password_hasher and password_hash.startswith('$argon2'):
            try:
                self.password_hasher.verify(password_hash, password)
                return True
            except VerifyMismatchError:
                return False
        else:
            # Fallback to werkzeug
            return check_password_hash(password_hash, password)

    def purge_stored_share2(self, conn):
        rows = conn.execute("""
            SELECT id, extra FROM messages
            WHERE type = ?
        """, ('share',)).fetchall()
        forbidden_keys = {'share2', 'share2_b64', 'share2_filename', 'share2_live'}

        for row in rows:
            extra = json.loads(row['extra'] or '{}')
            cleaned = {
                key: value
                for key, value in extra.items()
                if key not in forbidden_keys
            }
            if cleaned != extra:
                if self.use_postgres:
                    conn.execute(
                        "UPDATE messages SET extra = %s WHERE id = %s",
                        (json.dumps(cleaned), row['id'])
                    )
                else:
                    conn.execute(
                        "UPDATE messages SET extra = ? WHERE id = ?",
                        (json.dumps(cleaned), row['id'])
                    )

    def cleanup_old_messages(self):
        cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        with self._get_connection() as conn:
            if self.use_postgres:
                conn.execute("DELETE FROM messages WHERE created_at < %s", (cutoff,))
                # Also clean up expired shares
                conn.execute("DELETE FROM messages WHERE type = 'share' AND expires_at < %s", (datetime.utcnow().isoformat(),))
            else:
                conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
                conn.execute("DELETE FROM messages WHERE type = 'share' AND expires_at < ?", (datetime.utcnow().isoformat(),))

    def create_user(self, username, password):
        username = (username or '').strip()
        password = password or ''
        if not USERNAME_RE.fullmatch(username):
            return None, 'Username must be 3-30 letters, numbers, dots, dashes, or underscores'
        if len(password) < 8:
            return None, 'Password must be at least 8 characters'
        if len(password) > 128:
            return None, 'Password is too long'
        if password.lower() == username.lower():
            return None, 'Password cannot match username'

        user = {
            'id': str(uuid.uuid4()),
            'username': username,
            'password_hash': self._hash_password(password),
            'created_at': datetime.utcnow().isoformat(),
        }

        try:
            with self._get_connection() as conn:
                if self.use_postgres:
                    conn.execute("""
                        INSERT INTO users (id, username, password_hash, profile_image, last_seen, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (user['id'], user['username'], user['password_hash'], '', user['created_at'], user['created_at']))
                else:
                    conn.execute("""
                        INSERT INTO users (id, username, password_hash, profile_image, last_seen, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (user['id'], user['username'], user['password_hash'], '', user['created_at'], user['created_at']))
        except Exception as exc:
            if self.use_postgres:
                if 'duplicate key value violates unique constraint' in str(exc):
                    return None, 'Username already exists'
            else:
                if isinstance(exc, sqlite3.IntegrityError):
                    return None, 'Username already exists'
            return None, 'Registration failed'

        return {'id': user['id'], 'username': user['username'], 'profile_image': '', 'last_seen': user['created_at']}, None

    def authenticate(self, username, password):
        username = (username or '').strip()
        with self._get_connection() as conn:
            if self.use_postgres:
                row = conn.execute("SELECT * FROM users WHERE username = %s", (username,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if not row or not self._verify_password(password or '', row['password_hash']):
            return None, 'Invalid username or password'  # [MODIFIED] Generic error message

        self.update_last_seen(row['id'])
        return {
            'id': row['id'],
            'username': row['username'],
            'profile_image': row['profile_image'] or '',
            'last_seen': row['last_seen'],
        }, None

    def add_user(self, session_id, user):
        if session_id in self.sessions:
            self.remove_user(session_id)
        self.sessions[session_id] = user
        self.user_sessions.setdefault(user['id'], set()).add(session_id)

    def remove_user(self, session_id):
        user = self.sessions.pop(session_id, None)
        if not user:
            return None

        sessions = self.user_sessions.get(user['id'])
        if sessions:
            sessions.discard(session_id)
            if not sessions:
                self.update_last_seen(user['id'])
                del self.user_sessions[user['id']]

        return user

    def get_session_user(self, session_id):
        return self.sessions.get(session_id)

    def get_user(self, user_id):
        with self._get_connection() as conn:
            if self.use_postgres:
                row = conn.execute("SELECT id, username, profile_image, last_seen FROM users WHERE id = %s", (user_id,)).fetchone()
            else:
                row = conn.execute("SELECT id, username, profile_image, last_seen FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_users_list(self, current_user_id=None):
        with self._get_connection() as conn:
            if self.use_postgres:
                rows = conn.execute("SELECT id, username, profile_image, last_seen FROM users ORDER BY username COLLATE 'C'").fetchall()
            else:
                rows = conn.execute("SELECT id, username, profile_image, last_seen FROM users ORDER BY username COLLATE NOCASE").fetchall()

        users = []
        for row in rows:
            if row['id'] == current_user_id:
                continue
            users.append({
                'id': row['id'],
                'username': row['username'],
                'profile_image': row['profile_image'] or '',
                'online': row['id'] in self.user_sessions,
                'last_seen': row['last_seen'],
            })
        return users

    def update_profile_image(self, user_id, profile_image):
        with self._get_connection() as conn:
            if self.use_postgres:
                conn.execute("UPDATE users SET profile_image = %s WHERE id = %s", (profile_image, user_id))
            else:
                conn.execute("UPDATE users SET profile_image = ? WHERE id = ?", (profile_image, user_id))

    def update_last_seen(self, user_id):
        seen_at = datetime.utcnow().isoformat()
        with self._get_connection() as conn:
            if self.use_postgres:
                conn.execute("UPDATE users SET last_seen = %s WHERE id = %s", (seen_at, user_id))
            else:
                conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (seen_at, user_id))
        return seen_at

    def add_message(self, sender_id, recipient_id, msg_type, content, extra=None):
        self.cleanup_old_messages()
        sender = self.get_user(sender_id)
        recipient = self.get_user(recipient_id)
        if not sender or not recipient:
            return None

        created_at = datetime.utcnow().isoformat()
        expires_at = (datetime.fromisoformat(created_at) + timedelta(hours=1)).isoformat()

        message = {
            'id': secrets.token_urlsafe(12),
            'sender_id': sender_id,
            'recipient_id': recipient_id,
            'username': sender['username'],
            'recipient_username': recipient['username'],
            'type': msg_type,
            'content': content,
            'extra': extra or {},
            'read_at': None,
            'timestamp': datetime.fromisoformat(created_at).strftime('%H:%M:%S'),
            'created_at': created_at,
            'expires_at': expires_at,
            'share1_accessed': False
        }

        with self._get_connection() as conn:
            if self.use_postgres:
                conn.execute("""
                    INSERT INTO messages (id, sender_id, recipient_id, type, content, extra, read_at, created_at, expires_at, share1_accessed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    message['id'],
                    sender_id,
                    recipient_id,
                    msg_type,
                    content,
                    json.dumps(message['extra']),
                    message['read_at'],
                    created_at,
                    expires_at,
                    message['share1_accessed']
                ))
            else:
                conn.execute("""
                    INSERT INTO messages (id, sender_id, recipient_id, type, content, extra, read_at, created_at, expires_at, share1_accessed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    message['id'],
                    sender_id,
                    recipient_id,
                    msg_type,
                    content,
                    json.dumps(message['extra']),
                    message['read_at'],
                    created_at,
                    expires_at,
                    message['share1_accessed']
                ))

        return message

    def get_conversation(self, user_id, recipient_id):
        self.cleanup_old_messages()
        with self._get_connection() as conn:
            if self.use_postgres:
                rows = conn.execute("""
                    SELECT
                        messages.*,
                        sender.username AS sender_username,
                        recipient.username AS recipient_username
                    FROM messages
                    JOIN users AS sender ON sender.id = messages.sender_id
                    JOIN users AS recipient ON recipient.id = messages.recipient_id
                    WHERE
                        (sender_id = %s AND recipient_id = %s)
                        OR (sender_id = %s AND recipient_id = %s)
                    ORDER BY created_at ASC
                """, (user_id, recipient_id, recipient_id, user_id)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT
                        messages.*,
                        sender.username AS sender_username,
                        recipient.username AS recipient_username
                    FROM messages
                    JOIN users AS sender ON sender.id = messages.sender_id
                    JOIN users AS recipient ON recipient.id = messages.recipient_id
                    WHERE
                        (sender_id = ? AND recipient_id = ?)
                        OR (sender_id = ? AND recipient_id = ?)
                    ORDER BY created_at ASC
                """, (user_id, recipient_id, recipient_id, user_id)).fetchall()

        messages = []
        for row in rows:
            created_at = datetime.fromisoformat(row['created_at'])
            extra = json.loads(row['extra'] or '{}')
            messages.append({
                'id': row['id'],
                'sender_id': row['sender_id'],
                'recipient_id': row['recipient_id'],
                'username': row['sender_username'],
                'recipient_username': row['recipient_username'],
                'type': row['type'],
                'content': row['content'],
                'extra': extra,
                'read_at': row['read_at'],
                'timestamp': created_at.strftime('%H:%M:%S'),
                'created_at': row['created_at'],
                'expires_at': row['expires_at'],
                'share1_accessed': row['share1_accessed'] if 'share1_accessed' in row.keys() else False,
                'is_legacy': row['type'] in ['text', 'voice'] and not extra.get('encrypted', False)  # [NEW] Legacy detection
            })
        return messages

    def get_message_for_user(self, message_id, user_id):
        with self._get_connection() as conn:
            if self.use_postgres:
                row = conn.execute("""
                    SELECT
                        messages.*,
                        sender.username AS sender_username,
                        recipient.username AS recipient_username
                    FROM messages
                    JOIN users AS sender ON sender.id = messages.sender_id
                    JOIN users AS recipient ON recipient.id = messages.recipient_id
                    WHERE messages.id = %s
                      AND (messages.sender_id = %s OR messages.recipient_id = %s)
                """, (message_id, user_id, user_id)).fetchone()
            else:
                row = conn.execute("""
                    SELECT
                        messages.*,
                        sender.username AS sender_username,
                        recipient.username AS recipient_username
                    FROM messages
                    JOIN users AS sender ON sender.id = messages.sender_id
                    JOIN users AS recipient ON recipient.id = messages.recipient_id
                    WHERE messages.id = ?
                      AND (messages.sender_id = ? OR messages.recipient_id = ?)
                """, (message_id, user_id, user_id)).fetchone()

        if not row:
            return None

        created_at = datetime.fromisoformat(row['created_at'])
        extra = json.loads(row['extra'] or '{}')

        # [NEW] Handle one-time access for share1
        share1_token = extra.get('share1_token')
        if share1_token and row['type'] == 'share':
            # Mark as accessed if this is the first access
            if not row['share1_accessed']:
                if self.use_postgres:
                    conn.execute("UPDATE messages SET share1_accessed = TRUE WHERE id = %s", (message_id,))
                else:
                    conn.execute("UPDATE messages SET share1_accessed = TRUE WHERE id = ?", (message_id,))
                extra['share1_accessed'] = True

        return {
            'id': row['id'],
            'sender_id': row['sender_id'],
            'recipient_id': row['recipient_id'],
            'username': row['sender_username'],
            'recipient_username': row['recipient_username'],
            'type': row['type'],
            'content': row['content'],
            'extra': extra,
            'read_at': row['read_at'],
            'timestamp': created_at.strftime('%H:%M:%S'),
            'created_at': row['created_at'],
            'expires_at': row['expires_at'],
            'share1_accessed': row['share1_accessed'] if 'share1_accessed' in row.keys() else False,
            'is_legacy': row['type'] in ['text', 'voice'] and not extra.get('encrypted', False)  # [NEW] Legacy detection
        }

    def update_message_extra(self, message_id, extra):
        with self._get_connection() as conn:
            if self.use_postgres:
                conn.execute(
                    "UPDATE messages SET extra = %s WHERE id = %s",
                    (json.dumps(extra), message_id)
                )
            else:
                conn.execute(
                    "UPDATE messages SET extra = ? WHERE id = ?",
                    (json.dumps(extra), message_id)
                )

    def set_reaction(self, message_id, user_id, emoji):
        message = self.get_message_for_user(message_id, user_id)
        if not message:
            return None

        extra = message['extra']
        reactions = extra.setdefault('reactions', {})
        for users in reactions.values():
            if user_id in users:
                users.remove(user_id)
        if emoji:
            reactions.setdefault(emoji, [])
            if user_id not in reactions[emoji]:
                reactions[emoji].append(user_id)
        extra['reactions'] = {
            reaction: users
            for reaction, users in reactions.items()
            if users
        }
        self.update_message_extra(message_id, extra)
        message['extra'] = extra
        return message

    def mark_messages_read(self, reader_id, sender_id):
        read_at = datetime.utcnow().isoformat()
        with self._get_connection() as conn:
            if self.use_postgres:
                rows = conn.execute("""
                    SELECT id FROM messages
                    WHERE sender_id = %s AND recipient_id = %s AND read_at IS NULL
                """, (sender_id, reader_id)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id FROM messages
                    WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL
                """, (sender_id, reader_id)).fetchall()
            message_ids = [row['id'] for row in rows]
            if message_ids:
                if self.use_postgres:
                    conn.execute("""
                        UPDATE messages
                        SET read_at = %s
                        WHERE sender_id = %s AND recipient_id = %s AND read_at IS NULL
                    """, (read_at, sender_id, reader_id))
                else:
                    conn.execute("""
                        UPDATE messages
                        SET read_at = ?
                        WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL
                    """, (read_at, sender_id, reader_id))
        return message_ids, read_at

    def get_share_message_by_token(self, message_id, token):
        """[MODIFIED] Get share message with one-time access support."""
        with self._get_connection() as conn:
            if self.use_postgres:
                row = conn.execute("""
                    SELECT * FROM messages
                    WHERE id = %s AND type = %s
                """, (message_id, 'share')).fetchone()
            else:
                row = conn.execute("""
                    SELECT * FROM messages
                    WHERE id = ? AND type = ?
                """, (message_id, 'share')).fetchone()

        if not row:
            return None

        extra = json.loads(row['extra'] or '{}')
        share1_token = extra.get('share1_token')

        # [NEW] Check token and one-time access
        if not share1_token or not secrets.compare_digest(share1_token, token):
            return None

        # [NEW] Check if already accessed (one-time)
        if extra.get('share1_accessed', False):
            return None  # Already accessed

        return {
            'id': row['id'],
            'sender_id': row['sender_id'],
            'recipient_id': row['recipient_id'],
            'extra': extra,
        }

    def revoke_share_access(self, message_id):
        """[NEW] Revoke access to a share (mark as accessed)."""
        with self._get_connection() as conn:
            if self.use_postgres:
                conn.execute("UPDATE messages SET share1_accessed = TRUE WHERE id = %s AND type = 'share'", (message_id,))
            else:
                conn.execute("UPDATE messages SET share1_accessed = TRUE WHERE id = ? AND type = 'share'", (message_id,))

    def get_sessions_for_user(self, user_id):
        return list(self.user_sessions.get(user_id, set()))

    def delete_message(self, message_id, user_id):
        """Delete a message if user is sender or recipient."""
        with self._get_connection() as conn:
            if self.use_postgres:
                row = conn.execute("""
                    SELECT sender_id, recipient_id, extra FROM messages
                    WHERE id = %s
                """, (message_id,)).fetchone()
            else:
                row = conn.execute("""
                    SELECT sender_id, recipient_id, extra FROM messages
                    WHERE id = ?
                """, (message_id,)).fetchone()

            if not row:
                return False

            # Only allow sender or recipient to delete
            if user_id not in (row['sender_id'], row['recipient_id']):
                return False

            # If it's a share message, also delete the stored Share 1 file
            if row['extra']:
                try:
                    extra = json.loads(row['extra'])
                    share1_filename = extra.get('share1_filename')
                    if share1_filename:
                        share_path = os.path.join('app', 'shares', share1_filename)
                        if os.path.isfile(share_path):
                            os.remove(share_path)
                except Exception:
                    pass

            if self.use_postgres:
                conn.execute("DELETE FROM messages WHERE id = %s", (message_id,))
            else:
                conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            return True

    def delete_user_messages(self, user_id):
        """Delete all messages from/to a user."""
        with self._get_connection() as conn:
            # Get all share messages to delete their files
            if self.use_postgres:
                rows = conn.execute("""
                    SELECT extra FROM messages
                    WHERE (sender_id = %s OR recipient_id = %s)
                    AND type = %s
                """, (user_id, user_id, 'share')).fetchall()
            else:
                rows = conn.execute("""
                    SELECT extra FROM messages
                    WHERE (sender_id = ? OR recipient_id = ?)
                    AND type = ?
                """, (user_id, user_id, 'share')).fetchall()

            for row in rows:
                try:
                    extra = json.loads(row['extra'] or '{}')
                    share1_filename = extra.get('share1_filename')
                    if share1_filename:
                        share_path = os.path.join('app', 'shares', share1_filename)
                        if os.path.isfile(share_path):
                            os.remove(share_path)
                except Exception:
                    pass

            if self.use_postgres:
                conn.execute("DELETE FROM messages WHERE sender_id = %s OR recipient_id = %s", (user_id, user_id))
            else:
                conn.execute("DELETE FROM messages WHERE sender_id = ? OR recipient_id = ?", (user_id, user_id))

    def delete_user(self, user_id):
        """Delete a user account."""
        with self._get_connection() as conn:
            if self.use_postgres:
                conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
            else:
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


chat_room = ChatRoom()
