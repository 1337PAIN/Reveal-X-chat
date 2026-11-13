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
import base64
import binascii
import hashlib
import secrets
import sqlite3
import threading
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from werkzeug.security import check_password_hash, generate_password_hash

from app.timeutil import utcnow

try:
    import psycopg2
    import psycopg2.extras
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

# A UNIQUE index violation, on either backend. Used where the database, not
# a prior SELECT, is what decides that an insert is a duplicate.
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + (
    (psycopg2.IntegrityError,) if POSTGRES_AVAILABLE else ())

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    ARGON2_AVAILABLE = True
except ImportError:
    ARGON2_AVAILABLE = False


USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,30}$')
MESSAGE_TTL_HOURS = 1
#: How long a reconnecting socket may get its session back without signing in
#: again (and without a second TOTP prompt). Anything that changes who the
#: account is or what it may do ends this early: see bump_session_epoch.
RESUME_TOKEN_TTL_HOURS = 12
MAX_BIO_LENGTH = 150
USER_STATUSES = ('online', 'away', 'dnd')

# Keys that must never survive in the messages.extra blob. Share 2 is delivered
# live to the recipient and is not allowed to reach server storage.
FORBIDDEN_EXTRA_KEYS = {'share2', 'share2_b64', 'share2_filename', 'share2_live'}


# Verified against when there is no real hash to check, so that rejecting an
# unknown username costs the same Argon2 work as rejecting a wrong password.
# Built on first use with the store's own hasher, so its parameters (and so
# its cost) always match real hashes; see ChatRoom._dummy_password_hash.
_DUMMY_PASSWORD_HASH = None
_DUMMY_PASSWORD_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Secrets at rest
#
# A TOTP secret cannot be hashed like a password -- the server has to read it
# back to check a code -- so it was stored in the clear, and any copy of the
# database (a backup, a dump, a hosting snapshot) could mint valid codes for
# every account that had one. With REVEALX_DATA_KEY set, each secret is
# AES-GCM encrypted with the user's id as associated data, so a ciphertext
# copied onto another row does not open either.
# ---------------------------------------------------------------------------

SEALED_PREFIX = 'enc:v1:'


def _data_key():
    """The AESGCM for REVEALX_DATA_KEY, or None when it is not set.

    Read on each call rather than once at import, so the setting can change
    between tests and the error names the variable at the point of use.
    """
    raw = os.environ.get('REVEALX_DATA_KEY', '').strip()
    if not raw:
        return None
    # Standard or URL-safe alphabet, padding optional.
    normalised = raw.replace('-', '+').replace('_', '/')
    try:
        key = base64.b64decode(normalised + '=' * (-len(normalised) % 4), validate=True)
    except (binascii.Error, ValueError):
        key = b''
    if len(key) != 32:
        raise RuntimeError('REVEALX_DATA_KEY must be 32 random bytes, base64-encoded '
                           '(for example: python -c "import secrets,base64;'
                           'print(base64.b64encode(secrets.token_bytes(32)).decode())")')
    return AESGCM(key)


def _secret_aad(user_id):
    return f'revealx-totp|{user_id}'.encode('utf-8')


def seal_secret(user_id, secret):
    """Encrypt a secret for storage, or return it unchanged without a key."""
    if not secret:
        return ''
    box = _data_key()
    if box is None:
        return secret
    nonce = secrets.token_bytes(12)
    sealed = box.encrypt(nonce, secret.encode('utf-8'), _secret_aad(user_id))
    return SEALED_PREFIX + base64.b64encode(nonce + sealed).decode('ascii')


def open_secret(user_id, stored):
    """Decrypt a stored secret. Raises RuntimeError when it cannot be read.

    Never returns '' for an unreadable value: '' means "no second factor",
    and a missing key turning every account's 2FA off is the failure this
    exists to prevent.
    """
    if not stored or not stored.startswith(SEALED_PREFIX):
        return stored or ''
    box = _data_key()
    if box is None:
        raise RuntimeError('An encrypted TOTP secret was found but REVEALX_DATA_KEY is not set')
    try:
        blob = base64.b64decode(stored[len(SEALED_PREFIX):], validate=True)
        return box.decrypt(blob[:12], blob[12:], _secret_aad(user_id)).decode('utf-8')
    except (InvalidTag, binascii.Error, ValueError) as exc:
        raise RuntimeError('A TOTP secret could not be decrypted: REVEALX_DATA_KEY is wrong, '
                           'or the stored value was altered or moved') from exc


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
        self._db_dir_ready = False
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
        # The directory only has to exist once. This ran on every connection,
        # and add_message alone opens four -- a syscall per connection for a
        # condition that cannot become false while the process is running.
        # Measured at ~4% of the time spent sending a message.
        if not self._db_dir_ready:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._db_dir_ready = True
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
                    created_at TEXT NOT NULL,
                    session_epoch INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Only a SHA-256 of each token is kept, so a copy of the database
            # cannot be used to resume anybody's session.
            db.execute("""
                CREATE TABLE IF NOT EXISTS resume_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_epoch INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
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
                    deleted_by TEXT NOT NULL DEFAULT '[]',
                    msg_uuid_key TEXT
                )
            """)

            db.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    only_admins_post BOOLEAN NOT NULL DEFAULT FALSE,
                    only_admins_edit_info BOOLEAN NOT NULL DEFAULT TRUE
                )
            """)
            # No foreign keys, for the same reason the rest of the schema has
            # none: SQLite does not enforce them unless asked per connection,
            # so relying on them here would be a guarantee only on Postgres.
            # purge_user() does the cleanup explicitly instead.
            db.execute("""
                CREATE TABLE IF NOT EXISTS group_members (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, user_id)
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
                # A group message is stored as one row per member, so every
                # existing read filter, the expiry sweep, per-recipient read
                # receipts and the deletion scope all keep working unchanged.
                # group_message_id ties the fan-out back together for the UI.
                ('group_id', "ALTER TABLE messages ADD COLUMN group_id TEXT"),
                ('group_message_id', "ALTER TABLE messages ADD COLUMN group_message_id TEXT"),
                # "<lower user id>|<higher user id>|<msg_uuid>" for E2EE v2
                # messages, NULL otherwise. One column rather than three so a
                # single UNIQUE index covers the conversation in both
                # directions; see message_uuid_key().
                ('msg_uuid_key', "ALTER TABLE messages ADD COLUMN msg_uuid_key TEXT"),
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
                # Bumped whenever outstanding resume tokens must stop working.
                ('session_epoch', "ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in user_columns:
                    db.execute(ddl)

            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_firebase_uid ON users(firebase_uid)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_expires ON messages(expires_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_resume_tokens_user ON resume_tokens(user_id)")
            # NULLs are distinct in a UNIQUE index on both SQLite and Postgres,
            # so plaintext and v1 rows (no uuid) are unaffected.
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_msg_uuid ON messages(msg_uuid_key)")

            self._purge_stored_share2(db)
            self._migrate_secrets_at_rest(db)

    # ------------------------------------------------------------------
    # Passwords
    # ------------------------------------------------------------------

    def _hash_password(self, password):
        """Hash a password using argon2id, falling back to werkzeug."""
        if ARGON2_AVAILABLE and self.password_hasher:
            return self.password_hasher.hash(password)
        return generate_password_hash(password)

    def _dummy_password_hash(self):
        global _DUMMY_PASSWORD_HASH
        if _DUMMY_PASSWORD_HASH is None:
            with _DUMMY_PASSWORD_LOCK:
                if _DUMMY_PASSWORD_HASH is None:
                    _DUMMY_PASSWORD_HASH = self._hash_password(secrets.token_urlsafe(32))
        return _DUMMY_PASSWORD_HASH

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
        now = utcnow().isoformat()
        cutoff = (utcnow() - timedelta(hours=MESSAGE_TTL_HOURS)).isoformat()
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
            'created_at': utcnow().isoformat(),
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

        # No row, or a Google-only row with no password, used to return here
        # without hashing anything: ~0.3 ms against ~50 ms for a wrong
        # password on a real account. The message was identical and the
        # clock said which usernames exist. Both now pay for one verification
        # against a dummy hash, whose result is discarded.
        stored_hash = row['password_hash'] if row else ''
        if not stored_hash:
            self._verify_password(password or '', self._dummy_password_hash())
            return None, self.ERROR_BAD_CREDENTIALS
        if not self._verify_password(password or '', stored_hash):
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

    def is_active_user(self, user_id):
        """Does this id belong to an account that may receive anything?

        The roster already hides pending and disabled accounts, but every send
        path checked only that the recipient existed, so an unapproved or
        disabled account could still be written to by anyone holding its id.
        """
        if not user_id:
            return False
        with self._connect() as db:
            row = db.execute("SELECT account_state FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row) and (row['account_state'] or 'active') == 'active'

    def active_user_ids(self, user_ids):
        """The subset of `user_ids` that are active accounts."""
        return {uid for uid in (user_ids or ()) if self.is_active_user(uid)}

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
                created_at = utcnow().isoformat()
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
        return open_secret(user_id, row['totp_secret'] or '') if row else ''

    def set_totp_secret(self, user_id, secret):
        """Store (or clear, with None) the user's TOTP secret, encrypted when
        REVEALX_DATA_KEY is set."""
        with self._connect() as db:
            db.execute("UPDATE users SET totp_secret = ? WHERE id = ?",
                       (seal_secret(user_id, secret or ''), user_id))

    def _migrate_secrets_at_rest(self, db):
        """Bring stored TOTP secrets in line with REVEALX_DATA_KEY, or refuse.

        With a key: encrypt any plaintext secret, and prove the key opens the
        ones already encrypted. Without one: refuse to start if any are
        encrypted. Either failure raises, so a missing or wrong key stops the
        app at startup instead of making every second factor unreadable.
        """
        box = _data_key()
        rows = db.execute(
            "SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL AND totp_secret != ''"
        ).fetchall()
        sealed = [r for r in rows if (r['totp_secret'] or '').startswith(SEALED_PREFIX)]
        if box is None:
            if sealed:
                raise RuntimeError(
                    f'{len(sealed)} TOTP secret(s) are encrypted but REVEALX_DATA_KEY is not '
                    'set. Set it to the key they were written with.')
            return
        if sealed:
            # One is enough to show the key is the right one.
            try:
                open_secret(sealed[0]['id'], sealed[0]['totp_secret'])
            except RuntimeError as exc:
                raise RuntimeError('REVEALX_DATA_KEY does not open the stored TOTP secrets') from exc
        for row in rows:
            if not (row['totp_secret'] or '').startswith(SEALED_PREFIX):
                db.execute("UPDATE users SET totp_secret = ? WHERE id = ?",
                           (seal_secret(row['id'], row['totp_secret']), row['id']))

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
        seen_at = utcnow().isoformat()
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

    @staticmethod
    def message_uuid_key(user_a, user_b, msg_uuid):
        """The per-conversation identity of an E2EE v2 message.

        Sorted, so A->B and B->A share one namespace: a uuid already used in
        the conversation is refused whichever side sends it again.
        """
        low, high = sorted((user_a, user_b))
        return f'{low}|{high}|{msg_uuid}'

    def has_message_uuid(self, user_a, user_b, msg_uuid):
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM messages WHERE msg_uuid_key = ?",
                (self.message_uuid_key(user_a, user_b, msg_uuid),)
            ).fetchone()
        return row is not None

    def add_message(self, sender_id, recipient_id, msg_type, content, extra=None):
        """Store a direct message. Returns None when either account is gone,
        or when an E2EE v2 msg_uuid is already used in this conversation."""
        self.cleanup_old_messages()
        sender = self.get_user(sender_id)
        recipient = self.get_user(recipient_id)
        if not sender or not recipient:
            return None
        # Checked here as well as in the handlers, so a future send path that
        # forgets cannot write a row for an account that is not active.
        if not self.is_active_user(recipient_id):
            return None

        created_at = utcnow().isoformat()
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

        uuid_key = (self.message_uuid_key(sender_id, recipient_id, extra['msg_uuid'])
                    if extra.get('msg_uuid') else None)
        try:
            with self._connect() as db:
                db.execute("""
                    INSERT INTO messages (id, sender_id, recipient_id, type, content, extra,
                                          read_at, created_at, expires_at, share1_accessed,
                                          msg_uuid_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    message['id'], sender_id, recipient_id, msg_type, content,
                    json.dumps(extra), None, created_at, expires_at, False, uuid_key,
                ))
        except INTEGRITY_ERRORS:
            # The handler checks first, but two simultaneous sends both pass
            # that check. The index is what actually guarantees one row.
            if uuid_key is None:
                raise
            return None

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
        read_at = utcnow().isoformat()
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

        `consume=True` claims the share with a conditional UPDATE and returns it
        only to the request whose UPDATE changed the row.

        This used to SELECT, check share1_accessed in Python, then UPDATE. The
        docstring claimed that was one transaction; on SQLite it was not, since
        a plain SELECT opens none, so racing requests all read "not accessed"
        before any of them wrote and every one was handed Share 1. Measured: 8
        of 8 concurrent requests succeeded. The database now decides who wins,
        and exactly one UPDATE can match the unaccessed row.
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
            if row['expires_at'] and row['expires_at'] < utcnow().isoformat():
                return None
            # A recipient who deleted the message gave up their access to it.
            if row['recipient_id'] in _load_deleted_by(row):
                return None

            if consume:
                claimed = db.execute(
                    "UPDATE messages SET share1_accessed = ? "
                    "WHERE id = ? AND type = ? "
                    "AND (share1_accessed IS NULL OR share1_accessed = ?)",
                    (True, message_id, 'share', False)
                )
                if claimed.rowcount != 1:
                    return None

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

    def has_password(self, user_id):
        """Whether this account can be re-authenticated with a password.

        Firebase accounts are created with an empty password_hash -- they
        authenticate against Google and never hold a local secret. Asking one
        of them for a password is asking for something that does not exist, so
        anything gating an action on a password has to check this first and
        fall back to another proof.
        """
        with self._connect() as db:
            row = db.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return bool(row and row['password_hash'])

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

    # ------------------------------------------------------------------
    # Resume tokens
    # ------------------------------------------------------------------

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256((token or '').encode('utf-8')).hexdigest()

    def issue_resume_token(self, user_id):
        """A 256-bit bearer token that lets a reconnecting socket resume.

        Returned once, to the page that signed in, which keeps it in memory
        only. The database keeps its SHA-256 and the account's current
        session_epoch; bumping the epoch invalidates every token issued
        before it.
        """
        token = secrets.token_urlsafe(32)
        now = utcnow()
        with self._connect() as db:
            row = db.execute("SELECT session_epoch FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return None
            db.execute("DELETE FROM resume_tokens WHERE expires_at < ?", (now.isoformat(),))
            db.execute(
                "INSERT INTO resume_tokens (token_hash, user_id, session_epoch, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self._token_hash(token), user_id, row['session_epoch'] or 0,
                 (now + timedelta(hours=RESUME_TOKEN_TTL_HOURS)).isoformat(), now.isoformat()),
            )
        return token

    def redeem_resume_token(self, token):
        """The user id a live token belongs to, or None.

        Live means: known, not expired, issued in the account's current
        session epoch, and the account is still active.
        """
        if not token or len(token) > 128:
            return None
        with self._connect() as db:
            row = db.execute("""
                SELECT resume_tokens.user_id, resume_tokens.expires_at,
                       resume_tokens.session_epoch AS token_epoch,
                       users.session_epoch AS user_epoch, users.account_state
                FROM resume_tokens JOIN users ON users.id = resume_tokens.user_id
                WHERE resume_tokens.token_hash = ?
            """, (self._token_hash(token),)).fetchone()
        if not row:
            return None
        if row['expires_at'] < utcnow().isoformat():
            return None
        if (row['token_epoch'] or 0) != (row['user_epoch'] or 0):
            return None
        if (row['account_state'] or 'active') != 'active':
            return None
        return row['user_id']

    def bump_session_epoch(self, user_id):
        """End every resume token the account holds.

        Called on logout and on anything that changes who the account is or
        what it may do -- password reset, disable, role change, deletion --
        so a token issued before the change cannot carry the old session past
        it. The rows are deleted as well; the epoch is what makes that
        guarantee hold even for a token minted concurrently.
        """
        with self._connect() as db:
            db.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user_id,))
            db.execute("DELETE FROM resume_tokens WHERE user_id = ?", (user_id,))

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
        self.bump_session_epoch(user_id)
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
        self.bump_session_epoch(user_id)
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
        self.bump_session_epoch(user_id)
        return True, None


    # ------------------------------------------------------------------
    # Groups
    #
    # A group message is fanned out: one row per member, each carrying that
    # member's own ciphertext. Reveal-X encrypts pairwise -- ECDH between two
    # people -- so a group message is encrypted once per recipient rather than
    # with a shared group key. That costs N rows and N encryptions per message,
    # and in exchange it needs no new protocol and keeps the property that the
    # server stores ciphertext it cannot read.
    #
    # It also makes removal clean: a removed member simply stops being a
    # recipient, so they receive no further rows. Nothing has to be revoked.
    # ------------------------------------------------------------------

    GROUP_ROLE_RANK = {'member': 0, 'admin': 1, 'owner': 2}
    VALID_GROUP_ROLES = tuple(GROUP_ROLE_RANK)
    MAX_GROUP_NAME = 60
    MAX_GROUP_DESCRIPTION = 200
    MAX_GROUP_MEMBERS = 50

    def group_role_rank(self, role):
        return self.GROUP_ROLE_RANK.get(role or 'member', 0)

    def _row_to_group(self, row, role=None, member_count=None):
        return {
            'id': row['id'],
            'name': row['name'],
            'description': row['description'] or '',
            'created_by': row['created_by'],
            'created_at': row['created_at'],
            'only_admins_post': bool(row['only_admins_post']),
            'only_admins_edit_info': bool(row['only_admins_edit_info']),
            'role': role,
            'member_count': member_count,
            'is_group': True,
        }

    def create_group(self, creator_id, name, description='', member_ids=()):
        name = (name or '').strip()
        if not name:
            return None, 'A group needs a name'
        if len(name) > self.MAX_GROUP_NAME:
            return None, f'Name must be {self.MAX_GROUP_NAME} characters or fewer'
        description = (description or '').strip()[:self.MAX_GROUP_DESCRIPTION]
        if not self.get_user(creator_id):
            return None, 'No such account'

        # Deduplicate, drop the creator (added below as owner) and drop any id
        # that is not a real account, so a crafted list cannot seed membership
        # rows pointing at nothing.
        wanted = []
        for user_id in member_ids or ():
            if user_id and user_id != creator_id and user_id not in wanted:
                # Active only: pending and disabled accounts are not
                # contacts, and must not be pulled into a group either.
                if self.is_active_user(user_id):
                    wanted.append(user_id)
        if len(wanted) + 1 > self.MAX_GROUP_MEMBERS:
            return None, f'A group holds at most {self.MAX_GROUP_MEMBERS} members'

        now = utcnow().isoformat()
        group_id = secrets.token_urlsafe(12)
        with self._connect() as db:
            db.execute(
                "INSERT INTO groups (id, name, description, created_by, created_at,"
                " only_admins_post, only_admins_edit_info) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (group_id, name, description, creator_id, now, False, True),
            )
            db.execute(
                "INSERT INTO group_members (group_id, user_id, role, joined_at)"
                " VALUES (?, ?, ?, ?)",
                (group_id, creator_id, 'owner', now),
            )
            for user_id in wanted:
                db.execute(
                    "INSERT INTO group_members (group_id, user_id, role, joined_at)"
                    " VALUES (?, ?, ?, ?)",
                    (group_id, user_id, 'member', now),
                )
        return self.get_group(group_id, creator_id), None

    def get_group(self, group_id, viewer_id=None):
        with self._connect() as db:
            row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
            if not row:
                return None
            count = db.execute(
                "SELECT COUNT(*) AS n FROM group_members WHERE group_id = ?", (group_id,)
            ).fetchone()['n']
        return self._row_to_group(row, role=self.group_role(group_id, viewer_id) if viewer_id else None,
                                  member_count=int(count))

    def group_role(self, group_id, user_id):
        if not group_id or not user_id:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT role FROM group_members WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ).fetchone()
        return row['role'] if row else None

    def is_group_member(self, group_id, user_id):
        return self.group_role(group_id, user_id) is not None

    def get_group_members(self, group_id):
        with self._connect() as db:
            rows = db.execute(
                "SELECT group_members.user_id, group_members.role, group_members.joined_at,"
                "       users.username, users.profile_image, users.status"
                "  FROM group_members"
                "  JOIN users ON users.id = group_members.user_id"
                " WHERE group_members.group_id = ?"
                " ORDER BY group_members.joined_at ASC",
                (group_id,),
            ).fetchall()
        return [
            {
                'id': r['user_id'],
                'username': r['username'],
                'role': r['role'],
                'joined_at': r['joined_at'],
                'profile_image': r['profile_image'],
                'status': r['status'],
            }
            for r in rows
        ]

    def get_group_member_ids(self, group_id):
        with self._connect() as db:
            rows = db.execute(
                "SELECT user_id FROM group_members WHERE group_id = ?", (group_id,)
            ).fetchall()
        return [r['user_id'] for r in rows]

    def get_groups_for_user(self, user_id):
        with self._connect() as db:
            rows = db.execute(
                "SELECT groups.*, group_members.role AS viewer_role,"
                "       (SELECT COUNT(*) FROM group_members m WHERE m.group_id = groups.id) AS n"
                "  FROM groups"
                "  JOIN group_members ON group_members.group_id = groups.id"
                " WHERE group_members.user_id = ?"
                " ORDER BY groups.created_at DESC",
                (user_id,),
            ).fetchall()
        return [self._row_to_group(r, role=r['viewer_role'], member_count=int(r['n'])) for r in rows]

    def can_manage_in_group(self, group_id, actor_id, target_id):
        """May `actor` act on `target` inside this group?

        The same rule as the server-wide one in can_manage: strictly greater
        rank, never on yourself. An admin therefore cannot remove or demote
        another admin, and nobody can touch the owner.
        """
        if not actor_id or not target_id or actor_id == target_id:
            return False
        actor = self.group_role(group_id, actor_id)
        target = self.group_role(group_id, target_id)
        if actor is None or target is None:
            return False
        return self.group_role_rank(actor) > self.group_role_rank(target)

    def may_post_to_group(self, group_id, user_id):
        role = self.group_role(group_id, user_id)
        if role is None:
            return False
        group = self.get_group(group_id)
        if group and group['only_admins_post']:
            return self.group_role_rank(role) >= 1
        return True

    def may_edit_group_info(self, group_id, user_id):
        role = self.group_role(group_id, user_id)
        if role is None:
            return False
        group = self.get_group(group_id)
        if group and group['only_admins_edit_info']:
            return self.group_role_rank(role) >= 1
        return True

    def add_group_members(self, group_id, user_ids):
        added = []
        now = utcnow().isoformat()
        existing = set(self.get_group_member_ids(group_id))
        if len(existing) >= self.MAX_GROUP_MEMBERS:
            return [], f'A group holds at most {self.MAX_GROUP_MEMBERS} members'
        with self._connect() as db:
            for user_id in user_ids or ():
                if user_id in existing or not self.is_active_user(user_id):
                    continue
                if len(existing) + len(added) >= self.MAX_GROUP_MEMBERS:
                    break
                db.execute(
                    "INSERT INTO group_members (group_id, user_id, role, joined_at)"
                    " VALUES (?, ?, ?, ?)",
                    (group_id, user_id, 'member', now),
                )
                added.append(user_id)
        return added, None

    def remove_group_member(self, group_id, user_id):
        with self._connect() as db:
            db.execute(
                "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            )
        return True

    def set_group_role(self, group_id, user_id, role):
        """Promote to admin or demote to member.

        'owner' is not assignable here. There is exactly one owner and it moves
        only through transfer_group_ownership, for the same reason the
        superadmin rank is not assignable through set_role.
        """
        if role not in ('member', 'admin'):
            return False, 'Role must be member or admin'
        if self.group_role(group_id, user_id) == 'owner':
            return False, 'The group owner cannot be demoted'
        if not self.is_group_member(group_id, user_id):
            return False, 'That account is not in this group'
        with self._connect() as db:
            db.execute(
                "UPDATE group_members SET role = ? WHERE group_id = ? AND user_id = ?",
                (role, group_id, user_id),
            )
        return True, None

    def transfer_group_ownership(self, group_id, new_owner_id):
        if not self.is_group_member(group_id, new_owner_id):
            return False
        with self._connect() as db:
            db.execute(
                "UPDATE group_members SET role = 'member' WHERE group_id = ? AND role = 'owner'",
                (group_id,),
            )
            db.execute(
                "UPDATE group_members SET role = 'owner' WHERE group_id = ? AND user_id = ?",
                (group_id, new_owner_id),
            )
        return True

    def update_group_info(self, group_id, name=None, description=None):
        fields, params = [], []
        if name is not None:
            name = name.strip()
            if not name:
                return False, 'A group needs a name'
            if len(name) > self.MAX_GROUP_NAME:
                return False, f'Name must be {self.MAX_GROUP_NAME} characters or fewer'
            fields.append("name = ?")
            params.append(name)
        if description is not None:
            fields.append("description = ?")
            params.append(description.strip()[:self.MAX_GROUP_DESCRIPTION])
        if not fields:
            return False, 'Nothing to change'
        params.append(group_id)
        with self._connect() as db:
            db.execute(f"UPDATE groups SET {', '.join(fields)} WHERE id = ?", tuple(params))
        return True, None

    def update_group_settings(self, group_id, only_admins_post=None, only_admins_edit_info=None):
        fields, params = [], []
        if only_admins_post is not None:
            fields.append("only_admins_post = ?")
            params.append(bool(only_admins_post))
        if only_admins_edit_info is not None:
            fields.append("only_admins_edit_info = ?")
            params.append(bool(only_admins_edit_info))
        if not fields:
            return False, 'Nothing to change'
        params.append(group_id)
        with self._connect() as db:
            db.execute(f"UPDATE groups SET {', '.join(fields)} WHERE id = ?", tuple(params))
        return True, None

    def leave_group(self, group_id, user_id):
        """Returns (deleted_group, new_owner_id).

        An owner who leaves hands the group to the longest-serving admin, or
        failing that the longest-serving member. A group whose last member
        leaves is deleted rather than left ownerless and unreachable.
        """
        role = self.group_role(group_id, user_id)
        if role is None:
            return False, None
        members = [m for m in self.get_group_members(group_id) if m['id'] != user_id]
        if not members:
            self.delete_group(group_id)
            return True, None
        new_owner = None
        if role == 'owner':
            admins = [m for m in members if m['role'] == 'admin']
            new_owner = (admins or members)[0]['id']
        self.remove_group_member(group_id, user_id)
        if new_owner:
            self.transfer_group_ownership(group_id, new_owner)
        return False, new_owner

    def delete_group(self, group_id):
        with self._connect() as db:
            db.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
            db.execute("DELETE FROM messages WHERE group_id = ?", (group_id,))
            db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        return True

    def add_group_message(self, sender_id, group_id, msg_type, payloads, extra=None):
        """Fan a message out, one row per member.

        `payloads` maps a member id to {'content': ..., 'iv': ...}. Each
        member's copy is encrypted under that member's own pairwise key, so
        each carries its own IV. Only current members are written: an id that
        is not in the group is dropped rather than trusted, so a crafted
        payload map cannot deliver to outsiders.
        """
        self.cleanup_old_messages()
        sender = self.get_user(sender_id)
        group = self.get_group(group_id)
        if not sender or not group:
            return None
        # Images are one-to-one only. The visual cryptography split delivers
        # Share 2 live to a single recipient and never stores it, which cannot
        # be satisfied for a group where members are offline.
        if msg_type not in ('text', 'voice'):
            return None

        members = set(self.get_group_member_ids(group_id))
        if sender_id not in members:
            return None
        # A member disabled after joining stays in the group (reinstating them
        # restores it) but receives nothing while disabled.
        members = self.active_user_ids(members) | {sender_id}

        created_at = utcnow().isoformat()
        expires_at = (datetime.fromisoformat(created_at)
                      + timedelta(hours=MESSAGE_TTL_HOURS)).isoformat()
        extra = {k: v for k, v in (extra or {}).items() if k not in FORBIDDEN_EXTRA_KEYS}
        group_message_id = secrets.token_urlsafe(12)
        extra = dict(extra)
        extra['group_message_id'] = group_message_id

        written = []
        with self._connect() as db:
            for member_id in sorted(members):
                payload = (payloads or {}).get(member_id)
                if payload is None:
                    # No ciphertext for this member: skip rather than store a
                    # placeholder they could never decrypt.
                    continue
                if isinstance(payload, dict):
                    content = payload.get('content')
                    row_extra = dict(extra)
                    if payload.get('iv'):
                        row_extra['iv'] = payload['iv']
                else:
                    content, row_extra = payload, dict(extra)
                if not content:
                    continue
                row_id = secrets.token_urlsafe(12)
                db.execute(
                    "INSERT INTO messages (id, sender_id, recipient_id, type, content, extra,"
                    " read_at, created_at, expires_at, share1_accessed, group_id, group_message_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row_id, sender_id, member_id, msg_type, content, json.dumps(row_extra),
                     None, created_at, expires_at, False, group_id, group_message_id),
                )
                written.append((member_id, row_id))
        if not written:
            return None
        return {
            'group_id': group_id,
            'group_message_id': group_message_id,
            'sender_id': sender_id,
            'username': sender['username'],
            'type': msg_type,
            'created_at': created_at,
            'expires_at': expires_at,
            'rows': dict(written),
        }

    def get_group_conversation(self, group_id, user_id):
        """The rows addressed to this user in this group."""
        if not self.is_group_member(group_id, user_id):
            return []
        self.cleanup_old_messages()
        with self._connect() as db:
            rows = db.execute(self._JOINED_SELECT + """
                WHERE messages.group_id = ? AND messages.recipient_id = ?
                ORDER BY messages.created_at ASC
            """, (group_id, user_id)).fetchall()
        out = []
        for row in rows:
            if user_id in _load_deleted_by(row):
                continue
            message = self._row_to_message(row)
            message['group_id'] = group_id
            message['group_message_id'] = message['extra'].get('group_message_id')
            out.append(message)
        return out

    def get_system_message_recipients(self, group_id):
        return self.get_group_member_ids(group_id)

    def purge_user(self, user_id):
        """Delete an account and everything belonging to it.

        Messages and stored Share 1 files go first: dropping the user row alone
        would leave orphaned shares on disk that nothing can ever reach or
        clean up.
        """
        self.delete_user_messages(user_id)
        # Hand on or dissolve any group this account owns before the row goes,
        # or the group is left with an owner that does not exist and nobody
        # able to manage it.
        for group in self.get_groups_for_user(user_id):
            self.leave_group(group['id'], user_id)
        with self._connect() as db:
            db.execute("DELETE FROM resume_tokens WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM group_members WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True, None


chat_room = ChatRoom()
