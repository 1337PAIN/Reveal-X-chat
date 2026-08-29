"""
SQLite/PostgreSQL-backed accounts, online presence, and private chat storage.

Both backends share a single set of SQL statements. `_Db` adapts placeholders
and cursor handling, and `_connect()` guarantees commit/rollback/close so no
write is silently dropped and no PostgreSQL connection is leaked.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
import re
import secrets
import sqlite3
import uuid

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
MESSAGE_TTL_HOURS = 1
MAX_BIO_LENGTH = 150
USER_STATUSES = ('online', 'away', 'dnd')

# Keys that must never survive in the messages.extra blob. Share 2 is delivered
# live to the recipient and is not allowed to reach server storage.
FORBIDDEN_EXTRA_KEYS = {'share2', 'share2_b64', 'share2_filename', 'share2_live'}


def _load_deleted_by(row):
    """Read the per-user delete list, tolerating rows written before it existed."""
    if 'deleted_by' not in row.keys():
        return []
    try:
        return json.loads(row['deleted_by'] or '[]')
    except (TypeError, ValueError):
        return []


class _Db:
    """Thin cursor factory that hides SQLite/psycopg2 API differences."""

    def __init__(self, conn, use_postgres):
        self._conn = conn
        self._pg = use_postgres

    def execute(self, sql, params=()):
        if self._pg:
            # psycopg2 connections have no .execute(); they work through
            # cursors and use %s placeholders instead of ?.
            cursor = self._conn.cursor()
            cursor.execute(sql.replace('?', '%s'), params)
            return cursor
        return self._conn.execute(sql, params)


class ChatRoom:
    """Manages persistent users, expiring messages, and online sessions."""

    def __init__(self, db_path=None, shares_folder=None):
        self.database_url = os.environ.get('DATABASE_URL')
        if self.database_url and POSTGRES_AVAILABLE:
            self.use_postgres = True
            self.db_path = None
        else:
            self.use_postgres = False
            self.db_path = db_path or os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'data', 'reveal_x.db'
            )

        # Absolute so share cleanup works regardless of the process working dir.
        self.shares_folder = shares_folder or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'shares'
        )

        self.sessions = {}  # {session_id: user dict}
        self.user_sessions = {}  # {user_id: set(session_id)}
        self.init_db()

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

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    def _raw_connection(self):
        if self.use_postgres:
            return psycopg2.connect(
                self.database_url,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connect(self):
        """Yield a `_Db`, committing on success and always closing.

        `with sqlite3.connect(...)` only wraps a transaction and psycopg2's
        context manager does not close either, so both backends dropped work
        or leaked connections before this was centralised here.
        """
        conn = self._raw_connection()
        try:
            yield _Db(conn, self.use_postgres)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _existing_columns(self, db, table):
        if self.use_postgres:
            rows = db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table,)
            ).fetchall()
            return {row['column_name'] for row in rows}
        rows = db.execute("PRAGMA table_info(" + table + ")").fetchall()
        return {row['name'] for row in rows}

    def init_db(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    profile_image TEXT,
                    public_key TEXT,
                    bio TEXT,
                    status TEXT,
                    firebase_uid TEXT,
                    email TEXT,
                    auth_provider TEXT NOT NULL DEFAULT 'password',
                    totp_secret TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    account_state TEXT NOT NULL DEFAULT 'active',
                    last_seen TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("""
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
                    deleted_by TEXT NOT NULL DEFAULT '[]'
                )
            """)

            # Backfill columns for databases created by earlier versions.
            message_columns = self._existing_columns(db, 'messages')
            user_columns = self._existing_columns(db, 'users')
            for column, ddl in (
                ('expires_at', "ALTER TABLE messages ADD COLUMN expires_at TEXT"),
                ('read_at', "ALTER TABLE messages ADD COLUMN read_at TEXT"),
                ('share1_accessed', "ALTER TABLE messages ADD COLUMN share1_accessed BOOLEAN DEFAULT FALSE"),
                ('deleted_by', "ALTER TABLE messages ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in message_columns:
                    db.execute(ddl)
            for column, ddl in (
                ('last_seen', "ALTER TABLE users ADD COLUMN last_seen TEXT"),
                ('profile_image', "ALTER TABLE users ADD COLUMN profile_image TEXT"),
                ('public_key', "ALTER TABLE users ADD COLUMN public_key TEXT"),
                ('bio', "ALTER TABLE users ADD COLUMN bio TEXT"),
                ('status', "ALTER TABLE users ADD COLUMN status TEXT"),
                ('firebase_uid', "ALTER TABLE users ADD COLUMN firebase_uid TEXT"),
                ('email', "ALTER TABLE users ADD COLUMN email TEXT"),
                ('auth_provider', "ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'password'"),
                ('totp_secret', "ALTER TABLE users ADD COLUMN totp_secret TEXT"),
                # 'admin' or 'user'. Deliberately a column rather than a
                # hardcoded username, so the admin can be renamed.
                ('role', "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"),
                # 'pending' | 'active' | 'disabled'. Note this is NOT the
                # existing `status` column, which is presence (online/away/dnd).
                # Accounts that predate this migration default to 'active' so
                # an upgrade never locks anyone out.
                ('account_state', "ALTER TABLE users ADD COLUMN account_state TEXT NOT NULL DEFAULT 'active'"),
            ):
                if column not in user_columns:
                    db.execute(ddl)

            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_firebase_uid ON users(firebase_uid)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_expires ON messages(expires_at)")

            self._purge_stored_share2(db)

    # ------------------------------------------------------------------
    # Passwords
    # ------------------------------------------------------------------

    def _hash_password(self, password):
        """Hash a password using argon2id, falling back to werkzeug."""
        if ARGON2_AVAILABLE and self.password_hasher:
            return self.password_hasher.hash(password)
        return generate_password_hash(password)

    def _verify_password(self, password, password_hash):
        if not password_hash:
            return False
        if ARGON2_AVAILABLE and self.password_hasher and password_hash.startswith('$argon2'):
            try:
                self.password_hasher.verify(password_hash, password)
                return True
            except VerifyMismatchError:
                return False
            except Exception:
                return False
        return check_password_hash(password_hash, password)

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def _purge_stored_share2(self, db):
        rows = db.execute("SELECT id, extra FROM messages WHERE type = ?", ('share',)).fetchall()
        for row in rows:
            extra = json.loads(row['extra'] or '{}')
            cleaned = {
                key: value
                for key, value in extra.items()
                if key not in FORBIDDEN_EXTRA_KEYS
            }
            if cleaned != extra:
                db.execute(
                    "UPDATE messages SET extra = ? WHERE id = ?",
                    (json.dumps(cleaned), row['id'])
                )

    def _share_path(self, filename):
        """Resolve a stored Share 1 filename, rejecting path traversal."""
        if not filename or os.path.basename(filename) != filename:
            return None
        return os.path.join(self.shares_folder, filename)

    def _remove_share_file(self, extra_json):
        try:
            extra = json.loads(extra_json or '{}')
        except (TypeError, ValueError):
            return
        path = self._share_path(extra.get('share1_filename'))
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def cleanup_old_messages(self):
        """Delete expired messages and the Share 1 files they own."""
        now = datetime.utcnow().isoformat()
        cutoff = (datetime.utcnow() - timedelta(hours=MESSAGE_TTL_HOURS)).isoformat()
        with self._connect() as db:
            stale = db.execute(
                "SELECT extra FROM messages WHERE type = ? AND (created_at < ? OR expires_at < ?)",
                ('share', cutoff, now)
            ).fetchall()
            for row in stale:
                self._remove_share_file(row['extra'])

            db.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
            db.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(self, username, password, account_state='active', role='user'):
        """Create an account.

        `account_state` is 'pending' for self-service sign-up requests, which
        cannot sign in until an admin approves them, and 'active' when an admin
        creates the account directly.
        """
        if role not in self.VALID_ROLES:
            return None, 'Unknown role'
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
            with self._connect() as db:
                db.execute("""
                    INSERT INTO users (id, username, password_hash, profile_image, public_key,
                                       bio, status, role, account_state, last_seen, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (user['id'], user['username'], user['password_hash'], '', '', '', 'online',
                      role, account_state, user['created_at'], user['created_at']))
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) or 'duplicate key value' in str(exc).lower():
                return None, 'Username already exists'
            return None, 'Registration failed'

        return {
            'id': user['id'],
            'username': user['username'],
            'profile_image': '',
            'public_key': '',
            'bio': '',
            'status': 'online',
            'role': role,
            'account_state': account_state,
            'last_seen': user['created_at'],
        }, None

    # Sign-in failure reasons, named so callers can distinguish a wrong password
    # from a refusal that has nothing to do with the password. Only the first is
    # evidence of guessing.
    ERROR_BAD_CREDENTIALS = 'Invalid username or password'
    ERROR_PENDING = 'This account is waiting for administrator approval'
    ERROR_DISABLED = 'This account has been disabled by an administrator'

    def authenticate(self, username, password):
        username = (username or '').strip()
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if not row or not self._verify_password(password or '', row['password_hash']):
            return None, self.ERROR_BAD_CREDENTIALS

        # The password check comes first on purpose. Reporting "awaiting
        # approval" to someone who did not supply the right password would
        # confirm the username exists.
        keys = row.keys()
        state = (row['account_state'] if 'account_state' in keys else 'active') or 'active'
        if state == 'pending':
            return None, self.ERROR_PENDING
        if state == 'disabled':
            return None, self.ERROR_DISABLED

        self.update_last_seen(row['id'])
        return {
            'id': row['id'],
            'username': row['username'],
            'profile_image': row['profile_image'] or '',
            'public_key': (row['public_key'] if 'public_key' in keys else '') or '',
            'bio': (row['bio'] if 'bio' in keys else '') or '',
            'status': (row['status'] if 'status' in keys else '') or 'online',
            'role': (row['role'] if 'role' in keys else 'user') or 'user',
            'account_state': state,
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
        with self._connect() as db:
            row = db.execute(
                "SELECT id, username, profile_image, public_key, bio, status, last_seen FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_users_list(self, current_user_id=None):
        """The chat roster for one signed-in account.

        Two kinds of account are filtered out:

        * Not `active`. A request that has not been approved must not be
          visible to anyone but an admin, and a disabled account should
          disappear from everyone's list.
        * Administrators, unless the person asking is one themselves. The
          admin is a management account, not a contact; leaving it in meant
          every user could see it and watch it come online, which is both
          noise and a small piece of information nobody needs.

        Admins still see everyone, so the roster stays useful to them.
        """
        viewer_is_admin = bool(current_user_id) and self.is_admin(current_user_id)

        with self._connect() as db:
            rows = db.execute("""
                SELECT id, username, profile_image, public_key, bio, status, role, last_seen
                FROM users WHERE account_state = 'active' ORDER BY LOWER(username)
            """).fetchall()

        users = []
        for row in rows:
            if row['id'] == current_user_id:
                continue
            if not viewer_is_admin and self.role_rank(row['role']) >= 1:
                continue
            online = row['id'] in self.user_sessions
            users.append({
                'id': row['id'],
                'username': row['username'],
                'profile_image': row['profile_image'] or '',
                'public_key': (row['public_key'] or '') if 'public_key' in row.keys() else '',
                'bio': (row['bio'] or '') if 'bio' in row.keys() else '',
                # A chosen status only means anything while the user is connected.
                'status': ((row['status'] or 'online') if 'status' in row.keys() else 'online') if online else 'offline',
                'online': online,
                'last_seen': row['last_seen'],
            })
        return users

    # ------------------------------------------------------------------
    # Federated identity (Firebase) and TOTP
    # ------------------------------------------------------------------

    def _unique_username(self, db, desired):
        """Find a free username near `desired`, without clobbering anyone."""
        base = re.sub(r'[^A-Za-z0-9_.-]', '', (desired or '').strip()) or 'user'
        base = base[:26] or 'user'
        while len(base) < 3:
            base += '0'

        candidate = base
        for suffix in range(0, 1000):
            if suffix:
                candidate = f'{base[:26]}{suffix}'
            taken = db.execute(
                "SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)", (candidate,)
            ).fetchone()
            if not taken:
                return candidate
        return f'{base[:20]}{secrets.token_hex(4)}'

    def get_or_create_firebase_user(self, firebase_uid, email=None, display_name=None,
                                    provider='firebase'):
        """Map a verified Firebase account onto a local user row.

        Accounts are keyed by the Firebase UID only. Deliberately *not* matched
        on email: a provider that let someone sign up with an unverified address
        matching an existing account would otherwise hand them that account.
        """
        if not firebase_uid:
            return None, 'Missing Firebase account id'

        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE firebase_uid = ?", (firebase_uid,)
            ).fetchone()

            if row:
                # Keep the email fresh, but never silently rename the account.
                if email and (row['email'] or '') != email:
                    db.execute("UPDATE users SET email = ? WHERE id = ?", (email, row['id']))
                user_id = row['id']
            else:
                desired = display_name or (email.split('@')[0] if email else '') or 'user'
                username = self._unique_username(db, desired)
                created_at = datetime.utcnow().isoformat()
                user_id = str(uuid.uuid4())
                # A first-time Google/Firebase sign-in is an account *request*,
                # exactly like filling in the register form. Without this, an
                # approval policy would be trivially bypassable by anyone with
                # a Google account.
                db.execute("""
                    INSERT INTO users (id, username, password_hash, profile_image, public_key,
                                       bio, status, firebase_uid, email, auth_provider,
                                       role, account_state, last_seen, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (user_id, username, '', '', '', '', 'online',
                      firebase_uid, email or '', provider,
                      'user', 'pending', created_at, created_at))

        user = self.get_auth_user(user_id)
        if user and user['account_state'] != 'active':
            # Do not touch last_seen: they never got in.
            return None, ('This account is waiting for administrator approval'
                          if user['account_state'] == 'pending'
                          else 'This account has been disabled by an administrator')

        self.update_last_seen(user_id)
        return user, None

    def get_auth_user(self, user_id):
        """The user shape handed to the client after a successful sign-in."""
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        keys = row.keys()
        return {
            'id': row['id'],
            'username': row['username'],
            'profile_image': row['profile_image'] or '',
            'public_key': (row['public_key'] if 'public_key' in keys else '') or '',
            'bio': (row['bio'] if 'bio' in keys else '') or '',
            'status': (row['status'] if 'status' in keys else '') or 'online',
            'email': (row['email'] if 'email' in keys else '') or '',
            'auth_provider': (row['auth_provider'] if 'auth_provider' in keys else '') or 'password',
            'totp_enabled': bool(row['totp_secret'] if 'totp_secret' in keys else ''),
            'role': (row['role'] if 'role' in keys else 'user') or 'user',
            'account_state': (row['account_state'] if 'account_state' in keys else 'active') or 'active',
            'last_seen': row['last_seen'],
        }

    def get_totp_secret(self, user_id):
        with self._connect() as db:
            row = db.execute("SELECT totp_secret FROM users WHERE id = ?", (user_id,)).fetchone()
        return (row['totp_secret'] or '') if row else ''

    def set_totp_secret(self, user_id, secret):
        """Store (or clear, with None) the user's TOTP secret."""
        with self._connect() as db:
            db.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret or '', user_id))

    def update_username(self, user_id, new_username):
        """Rename an account, enforcing the same rules as registration."""
        new_username = (new_username or '').strip()
        if not USERNAME_RE.fullmatch(new_username):
            return None, 'Username must be 3-30 letters, numbers, dots, dashes, or underscores'

        current = self.get_user(user_id)
        if not current:
            return None, 'Account not found'
        if current['username'] == new_username:
            return current, None

        try:
            with self._connect() as db:
                # Case-insensitive check, so "Alice" cannot shadow "alice".
                clash = db.execute(
                    "SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND id <> ?",
                    (new_username, user_id)
                ).fetchone()
                if clash:
                    return None, 'Username already exists'
                db.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user_id))
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) or 'duplicate key value' in str(exc).lower():
                return None, 'Username already exists'
            return None, 'Could not update username'

        # Keep any live sessions in step so the header and outgoing messages
        # do not keep showing the old name until reload.
        for session_user in self.sessions.values():
            if session_user['id'] == user_id:
                session_user['username'] = new_username

        return self.get_user(user_id), None

    def update_bio(self, user_id, bio):
        bio = (bio or '').strip()[:MAX_BIO_LENGTH]
        with self._connect() as db:
            db.execute("UPDATE users SET bio = ? WHERE id = ?", (bio, user_id))
        return bio

    def update_status(self, user_id, status):
        """Set the user's chosen availability. Unknown values fall back to online."""
        status = (status or '').strip().lower()
        if status not in USER_STATUSES:
            status = 'online'
        with self._connect() as db:
            db.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
        for session_user in self.sessions.values():
            if session_user['id'] == user_id:
                session_user['status'] = status
        return status

    def update_profile_image(self, user_id, profile_image):
        with self._connect() as db:
            db.execute("UPDATE users SET profile_image = ? WHERE id = ?", (profile_image, user_id))

    def set_public_key(self, user_id, public_key):
        """Store the user's ECDH public key so peers can derive a shared secret."""
        with self._connect() as db:
            db.execute("UPDATE users SET public_key = ? WHERE id = ?", (public_key or '', user_id))

    def update_last_seen(self, user_id):
        seen_at = datetime.utcnow().isoformat()
        with self._connect() as db:
            db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (seen_at, user_id))
        return seen_at

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def _row_to_message(self, row):
        created_at = datetime.fromisoformat(row['created_at'])
        extra = json.loads(row['extra'] or '{}')
        keys = row.keys()
        return {
            'id': row['id'],
            'sender_id': row['sender_id'],
            'recipient_id': row['recipient_id'],
            'username': row['sender_username'] if 'sender_username' in keys else None,
            'recipient_username': row['recipient_username'] if 'recipient_username' in keys else None,
            'type': row['type'],
            'content': row['content'],
            'extra': extra,
            'read_at': row['read_at'],
            'timestamp': created_at.strftime('%H:%M:%S'),
            'created_at': row['created_at'],
            'expires_at': row['expires_at'],
            'share1_accessed': bool(row['share1_accessed']) if 'share1_accessed' in keys else False,
            # A message counts as encrypted only when the client actually
            # wrapped it with a derived key. Anything else is plaintext.
            'encrypted': bool(extra.get('encrypted', False)),
            'is_legacy': row['type'] in ('text', 'voice') and not extra.get('encrypted', False),
        }

    def add_message(self, sender_id, recipient_id, msg_type, content, extra=None):
        self.cleanup_old_messages()
        sender = self.get_user(sender_id)
        recipient = self.get_user(recipient_id)
        if not sender or not recipient:
            return None

        created_at = datetime.utcnow().isoformat()
        expires_at = (datetime.fromisoformat(created_at) + timedelta(hours=MESSAGE_TTL_HOURS)).isoformat()
        extra = {k: v for k, v in (extra or {}).items() if k not in FORBIDDEN_EXTRA_KEYS}

        message = {
            'id': secrets.token_urlsafe(12),
            'sender_id': sender_id,
            'recipient_id': recipient_id,
            'username': sender['username'],
            'recipient_username': recipient['username'],
            'type': msg_type,
            'content': content,
            'extra': extra,
            'read_at': None,
            'timestamp': datetime.fromisoformat(created_at).strftime('%H:%M:%S'),
            'created_at': created_at,
            'expires_at': expires_at,
            'share1_accessed': False,
            'encrypted': bool(extra.get('encrypted', False)),
            'is_legacy': msg_type in ('text', 'voice') and not extra.get('encrypted', False),
        }

        with self._connect() as db:
            db.execute("""
                INSERT INTO messages (id, sender_id, recipient_id, type, content, extra, read_at, created_at, expires_at, share1_accessed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message['id'], sender_id, recipient_id, msg_type, content,
                json.dumps(extra), None, created_at, expires_at, False,
            ))

        return message

    _JOINED_SELECT = """
        SELECT
            messages.*,
            sender.username AS sender_username,
            recipient.username AS recipient_username
        FROM messages
        JOIN users AS sender ON sender.id = messages.sender_id
        JOIN users AS recipient ON recipient.id = messages.recipient_id
    """

    def get_conversation(self, user_id, recipient_id):
        self.cleanup_old_messages()
        with self._connect() as db:
            rows = db.execute(self._JOINED_SELECT + """
                WHERE (sender_id = ? AND recipient_id = ?)
                   OR (sender_id = ? AND recipient_id = ?)
                ORDER BY created_at ASC
            """, (user_id, recipient_id, recipient_id, user_id)).fetchall()
        # Messages this user deleted stay in the table for the other side, but
        # must not come back into this user's transcript.
        return [
            self._row_to_message(row)
            for row in rows
            if user_id not in _load_deleted_by(row)
        ]

    def get_message_for_user(self, message_id, user_id):
        """Read a message the user participates in.

        This is a pure read: replies, reactions and forwards all call it, so it
        must never consume a share's one-time access.
        """
        with self._connect() as db:
            row = db.execute(self._JOINED_SELECT + """
                WHERE messages.id = ?
                  AND (messages.sender_id = ? OR messages.recipient_id = ?)
            """, (message_id, user_id, user_id)).fetchone()
        if not row or user_id in _load_deleted_by(row):
            return None
        return self._row_to_message(row)

    def update_message_extra(self, message_id, extra):
        extra = {k: v for k, v in (extra or {}).items() if k not in FORBIDDEN_EXTRA_KEYS}
        with self._connect() as db:
            db.execute("UPDATE messages SET extra = ? WHERE id = ?", (json.dumps(extra), message_id))

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
        with self._connect() as db:
            rows = db.execute("""
                SELECT id FROM messages
                WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL
            """, (sender_id, reader_id)).fetchall()
            message_ids = [row['id'] for row in rows]
            if message_ids:
                db.execute("""
                    UPDATE messages SET read_at = ?
                    WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL
                """, (read_at, sender_id, reader_id))
        return message_ids, read_at

    # ------------------------------------------------------------------
    # Share 1 access control
    # ------------------------------------------------------------------

    def get_share_message_by_token(self, message_id, token, consume=False):
        """Look up a share by its unguessable token.

        `consume=True` marks the share accessed inside the same transaction, so
        the one-time guarantee still holds when two requests race.
        """
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM messages WHERE id = ? AND type = ?",
                (message_id, 'share')
            ).fetchone()
            if not row:
                return None

            extra = json.loads(row['extra'] or '{}')
            stored_token = extra.get('share1_token')
            if not stored_token or not secrets.compare_digest(stored_token, token or ''):
                return None
            if row['share1_accessed']:
                return None
            if row['expires_at'] and row['expires_at'] < datetime.utcnow().isoformat():
                return None
            # A recipient who deleted the message gave up their access to it.
            if row['recipient_id'] in _load_deleted_by(row):
                return None

            if consume:
                db.execute("UPDATE messages SET share1_accessed = ? WHERE id = ?", (True, message_id))

            return {
                'id': row['id'],
                'sender_id': row['sender_id'],
                'recipient_id': row['recipient_id'],
                'extra': extra,
                'share1_accessed': bool(row['share1_accessed']),
            }

    def revoke_share_access(self, message_id):
        """Burn a share's remaining access without deleting the conversation."""
        with self._connect() as db:
            db.execute(
                "UPDATE messages SET share1_accessed = ? WHERE id = ? AND type = ?",
                (True, message_id, 'share')
            )

    def get_sessions_for_user(self, user_id):
        return list(self.user_sessions.get(user_id, set()))

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete_message(self, message_id, user_id):
        """Delete a message, with the scope decided by who is asking.

        The sender owns what they sent, so they delete it for everyone: the row
        and any stored Share 1 are destroyed on both sides. The recipient can
        only delete their own copy; the sender keeps theirs.

        Returns (scope, user_ids_to_notify) where scope is 'everyone' or 'self',
        or None when the user may not delete this message.
        """
        with self._connect() as db:
            row = db.execute(
                "SELECT sender_id, recipient_id, extra, deleted_by FROM messages WHERE id = ?",
                (message_id,)
            ).fetchone()
            if not row:
                return None

            sender_id, recipient_id = row['sender_id'], row['recipient_id']
            if user_id not in (sender_id, recipient_id):
                return None

            if user_id == sender_id:
                self._remove_share_file(row['extra'])
                db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                return 'everyone', (sender_id, recipient_id)

            # Recipient: hide it from them, leave the sender's copy alone.
            deleted_by = set(_load_deleted_by(row))
            if user_id in deleted_by:
                return 'self', (user_id,)

            deleted_by.add(user_id)
            db.execute(
                "UPDATE messages SET deleted_by = ? WHERE id = ?",
                (json.dumps(sorted(deleted_by)), message_id)
            )
            return 'self', (user_id,)

    def delete_user_messages(self, user_id):
        """Delete all messages from/to a user, including their Share 1 files."""
        with self._connect() as db:
            rows = db.execute("""
                SELECT extra FROM messages
                WHERE (sender_id = ? OR recipient_id = ?) AND type = ?
            """, (user_id, user_id, 'share')).fetchall()
            for row in rows:
                self._remove_share_file(row['extra'])

            db.execute("DELETE FROM messages WHERE sender_id = ? OR recipient_id = ?", (user_id, user_id))

    def delete_user(self, user_id):
        with self._connect() as db:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # ------------------------------------------------------------------
    # Administration
    #
    # Every method here is a raw capability -- none of them check who is
    # calling. Authorisation happens once, at the socket boundary in
    # routes.py (`_require_admin`), so there is a single place to audit
    # rather than a check duplicated across a dozen handlers.
    # ------------------------------------------------------------------

    VALID_ACCOUNT_STATES = ('pending', 'active', 'disabled')

    # Three ranks. An account may only act on one strictly below its own, which
    # is what stops an admin from disabling a peer or touching the superadmin.
    ROLE_RANK = {'user': 0, 'admin': 1, 'superadmin': 2}
    VALID_ROLES = tuple(ROLE_RANK)

    def get_role(self, user_id):
        with self._connect() as db:
            row = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        return (row['role'] or 'user') if row else None

    def role_rank(self, role):
        return self.ROLE_RANK.get(role or 'user', 0)

    def is_admin(self, user_id):
        """True for admins *and* the superadmin -- i.e. 'may open the panel'."""
        return self.role_rank(self.get_role(user_id)) >= 1

    def is_superadmin(self, user_id):
        return self.get_role(user_id) == 'superadmin'

    def can_manage(self, actor_id, target_id):
        """May `actor` act on `target`?

        Strictly-greater rank, and never on yourself. Self-management is
        excluded here rather than in each caller so that 'disable my own
        account' and 'demote myself' are both impossible by construction --
        either would be a way to lock the deployment out of its own admin.
        """
        if not actor_id or not target_id or actor_id == target_id:
            return False
        return self.role_rank(self.get_role(actor_id)) > self.role_rank(self.get_role(target_id))

    def set_role(self, user_id, role):
        """Promote or demote. The superadmin rank is not assignable here: it
        comes from the environment at startup and nowhere else, so there is
        exactly one and it cannot be granted through the UI."""
        if role not in ('user', 'admin'):
            return False, 'Role must be user or admin'
        if self.get_role(user_id) == 'superadmin':
            return False, 'The superadmin role cannot be changed'
        with self._connect() as db:
            row = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return False, 'No such account'
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        return True, None

    def count_admins(self, exclude_user_id=None):
        """Active accounts that can administer, superadmin included."""
        with self._connect() as db:
            if exclude_user_id:
                row = db.execute(
                    "SELECT COUNT(*) AS n FROM users "
                    "WHERE role IN ('admin', 'superadmin') AND account_state = 'active' AND id != ?",
                    (exclude_user_id,)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT COUNT(*) AS n FROM users "
                    "WHERE role IN ('admin', 'superadmin') AND account_state = 'active'"
                ).fetchone()
        return int(row['n']) if row else 0

    def ensure_admin(self, username, password):
        """Create or promote the bootstrap administrator.

        Called once at startup from the REVEALX_ADMIN_USER / _PASSWORD pair.
        Idempotent: on a restart it promotes and re-activates the existing
        account rather than failing on the unique username.

        It deliberately does NOT reset the password of an account that already
        exists. Otherwise anyone who could set an environment variable could
        silently take over an established admin account, and a stale value left
        in a shell profile would quietly revert a password the admin changed.
        Returns (user, created, error).
        """
        username = (username or '').strip()
        if not username:
            return None, False, 'No admin username configured'

        with self._connect() as db:
            # Exactly one superadmin, and only the environment can name it.
            # Demote any other account that somehow holds the rank, so a
            # restart with a different REVEALX_ADMIN_USER moves it rather than
            # silently accumulating superadmins.
            db.execute(
                "UPDATE users SET role = 'admin' "
                "WHERE role = 'superadmin' AND LOWER(username) != LOWER(?)",
                (username,)
            )
            row = db.execute("SELECT id, role, account_state FROM users WHERE LOWER(username) = LOWER(?)",
                             (username,)).fetchone()
            if row:
                db.execute(
                    "UPDATE users SET role = 'superadmin', account_state = 'active' WHERE id = ?",
                    (row['id'],)
                )
                return self.get_auth_user(row['id']), False, None

        user, error = self.create_user(username, password, account_state='active', role='superadmin')
        if error:
            return None, False, error
        return user, True, None

    def list_accounts(self):
        """Every account, including pending and disabled ones. Admin only."""
        with self._connect() as db:
            rows = db.execute("""
                SELECT id, username, role, account_state, email, auth_provider,
                       totp_secret, bio, profile_image, last_seen, created_at
                FROM users
                ORDER BY
                    CASE account_state WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                    LOWER(username)
            """).fetchall()

        accounts = []
        for row in rows:
            accounts.append({
                'id': row['id'],
                'username': row['username'],
                'role': (row['role'] or 'user'),
                'account_state': (row['account_state'] or 'active'),
                'email': row['email'] or '',
                'auth_provider': row['auth_provider'] or 'password',
                'totp_enabled': bool(row['totp_secret']),
                'profile_image': row['profile_image'] or '',
                'online': row['id'] in self.user_sessions,
                'last_seen': row['last_seen'],
                'created_at': row['created_at'],
            })
        return accounts

    def set_account_state(self, user_id, state):
        """Approve, disable or re-enable an account."""
        if state not in self.VALID_ACCOUNT_STATES:
            return False, 'Unknown account state'
        with self._connect() as db:
            row = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return False, 'No such account'
            db.execute("UPDATE users SET account_state = ? WHERE id = ?", (state, user_id))
        return True, None

    def set_password(self, user_id, new_password):
        """Replace an account's password.

        Reuses the same validation as registration so an admin cannot set a
        weaker password than a user could choose for themselves.
        """
        new_password = new_password or ''
        if len(new_password) < 8:
            return False, 'Password must be at least 8 characters'
        if len(new_password) > 128:
            return False, 'Password is too long'
        with self._connect() as db:
            row = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return False, 'No such account'
            if new_password.lower() == (row['username'] or '').lower():
                return False, 'Password cannot match username'
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (self._hash_password(new_password), user_id))
        return True, None

    def purge_user(self, user_id):
        """Delete an account and everything belonging to it.

        Messages and stored Share 1 files go first: dropping the user row alone
        would leave orphaned shares on disk that nothing can ever reach or
        clean up.
        """
        self.delete_user_messages(user_id)
        with self._connect() as db:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True, None


chat_room = ChatRoom()
