"""Versioned schema migrations.

The schema used to be built on every start by one function: CREATE TABLE IF
NOT EXISTS with the latest columns, followed by a list of ALTER TABLEs guarded
by "does this column exist yet?". It worked, but nothing recorded which
version a database was at, so there was no way to tell what an upgrade would
do to a given database, and a change that was not a new column -- a data
fix, a changed index -- had no natural place to go.

Each migration below is a numbered step, applied once and recorded in
`schema_version` with its name and when it ran. The early steps reproduce the
history of the old function, and every one of them is idempotent (IF NOT
EXISTS, or a column-exists check), because a database created before this
file existed has no `schema_version` table and so starts at 0: it replays
every step, and each step finds its work already done and skips it.

Add a migration by appending a step with the next number. Never edit or
reorder a step that has shipped: databases in the field have already run it.
"""

from app.timeutil import utcnow


def _add_columns(room, db, table, columns):
    existing = room._existing_columns(db, table)
    for column, ddl in columns:
        if column not in existing:
            db.execute(ddl)


def m001_base_tables(room, db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
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
            created_at TEXT NOT NULL
        )
    """)


def m002_message_lifecycle(room, db):
    _add_columns(room, db, 'messages', (
        ('expires_at', "ALTER TABLE messages ADD COLUMN expires_at TEXT"),
        ('read_at', "ALTER TABLE messages ADD COLUMN read_at TEXT"),
        ('share1_accessed', "ALTER TABLE messages ADD COLUMN share1_accessed BOOLEAN DEFAULT FALSE"),
        ('deleted_by', "ALTER TABLE messages ADD COLUMN deleted_by TEXT NOT NULL DEFAULT '[]'"),
    ))


def m003_profiles(room, db):
    _add_columns(room, db, 'users', (
        ('last_seen', "ALTER TABLE users ADD COLUMN last_seen TEXT"),
        ('profile_image', "ALTER TABLE users ADD COLUMN profile_image TEXT"),
        ('public_key', "ALTER TABLE users ADD COLUMN public_key TEXT"),
        ('bio', "ALTER TABLE users ADD COLUMN bio TEXT"),
        ('status', "ALTER TABLE users ADD COLUMN status TEXT"),
    ))


def m004_firebase_identity(room, db):
    _add_columns(room, db, 'users', (
        ('firebase_uid', "ALTER TABLE users ADD COLUMN firebase_uid TEXT"),
        ('email', "ALTER TABLE users ADD COLUMN email TEXT"),
        ('auth_provider', "ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'password'"),
    ))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_firebase_uid ON users(firebase_uid)")


def m005_totp(room, db):
    _add_columns(room, db, 'users', (
        ('totp_secret', "ALTER TABLE users ADD COLUMN totp_secret TEXT"),
    ))


def m006_roles_and_approval(room, db):
    _add_columns(room, db, 'users', (
        # 'admin' or 'user'. A column rather than a hardcoded username, so the
        # admin can be renamed.
        ('role', "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"),
        # 'pending' | 'active' | 'disabled' -- not `status`, which is presence.
        # Accounts that predate this default to 'active' so an upgrade never
        # locks anyone out.
        ('account_state', "ALTER TABLE users ADD COLUMN account_state TEXT NOT NULL DEFAULT 'active'"),
    ))


def m007_message_indexes(room, db):
    db.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_messages_expires ON messages(expires_at)")


def m008_groups(room, db):
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
    # No foreign keys: SQLite does not enforce them unless asked per
    # connection, so they would be a guarantee only on Postgres. purge_user()
    # does the cleanup explicitly instead.
    db.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id)
        )
    """)
    # A group message is one row per member, so the read filters, the expiry
    # sweep, read receipts and deletion scope all keep working unchanged.
    _add_columns(room, db, 'messages', (
        ('group_id', "ALTER TABLE messages ADD COLUMN group_id TEXT"),
        ('group_message_id', "ALTER TABLE messages ADD COLUMN group_message_id TEXT"),
    ))
    db.execute("CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id)")


def m009_e2ee_v2_message_uuid(room, db):
    # "<lower user id>|<higher user id>|<msg_uuid>" for E2EE v2 messages, NULL
    # otherwise. One column so one UNIQUE index covers the conversation in
    # both directions; NULLs are distinct on SQLite and Postgres alike.
    _add_columns(room, db, 'messages', (
        ('msg_uuid_key', "ALTER TABLE messages ADD COLUMN msg_uuid_key TEXT"),
    ))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_msg_uuid ON messages(msg_uuid_key)")


def m010_resume_tokens(room, db):
    # Bumped whenever outstanding resume tokens must stop working.
    _add_columns(room, db, 'users', (
        ('session_epoch', "ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0"),
    ))
    # Only a SHA-256 of each token is kept.
    db.execute("""
        CREATE TABLE IF NOT EXISTS resume_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_epoch INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_resume_tokens_user ON resume_tokens(user_id)")


def m011_contacts(room, db):
    """Contacts: who may see and message whom.

    One row per pair of accounts, the lower id first, so "are these two
    contacts?" is one primary-key lookup whichever of them asks. `status` is
    'pending' until the person who did not ask accepts, then 'accepted'.

    Before this table every account could message every other. So nobody
    loses a conversation they already had, each pair that has a direct
    message between them starts as accepted contacts. Group messages are not
    direct conversations and do not count. Done in Python rather than one
    INSERT ... SELECT so it reads the same on SQLite and PostgreSQL.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            user_low TEXT NOT NULL,
            user_high TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            accepted_at TEXT,
            PRIMARY KEY (user_low, user_high)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_contacts_high ON contacts(user_high)")

    columns = room._existing_columns(db, 'messages')
    direct = " AND group_id IS NULL" if 'group_id' in columns else ''
    user_ids = {row["id"] for row in db.execute("SELECT id FROM users").fetchall()}
    pairs = set()
    for row in db.execute(f"SELECT DISTINCT sender_id, recipient_id FROM messages WHERE sender_id <> recipient_id{direct}").fetchall():
        a, b = row['sender_id'], row['recipient_id']
        if a in user_ids and b in user_ids:
            pairs.add((min(a, b), max(a, b)))
    now = utcnow().isoformat()
    for low, high in sorted(pairs):
        exists = db.execute("SELECT 1 FROM contacts WHERE user_low = ? AND user_high = ?", (low, high)).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO contacts (user_low, user_high, requested_by, status, created_at, accepted_at) "
                "VALUES (?, ?, ?, 'accepted', ?, ?)",
                (low, high, low, now, now))


def case_duplicate_usernames(db):
    """Lower-cased usernames held by more than one account."""
    rows = db.execute(
        "SELECT LOWER(username) AS name FROM users GROUP BY LOWER(username) HAVING COUNT(*) > 1"
    ).fetchall()
    return sorted(row['name'] for row in rows)


def m012_username_case(room, db):
    """One account per username, whatever the capitals.

    The UNIQUE constraint on users.username compares exactly, so "Alice" and
    "alice" could both be created, while contacts, renaming and the admin
    tools all compare without case. A unique index on LOWER(username) makes
    the database refuse that; SQLite and PostgreSQL both index expressions.

    A database that already holds such a pair cannot take the index. It is
    then skipped and the names logged, so the server still starts and an
    administrator can rename one of each; create_user refuses new ones either
    way.
    """
    duplicates = case_duplicate_usernames(db)
    if duplicates:
        from app.log import log
        log.warning('Usernames held twice with different capitals; rename one of each, '
                    'then restart to add the index: %s', ', '.join(duplicates))
        return
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (LOWER(username))")


MIGRATIONS = [
    (1, 'base tables', m001_base_tables),
    (2, 'message lifecycle columns', m002_message_lifecycle),
    (3, 'profile columns', m003_profiles),
    (4, 'firebase identity', m004_firebase_identity),
    (5, 'totp secret', m005_totp),
    (6, 'roles and account approval', m006_roles_and_approval),
    (7, 'message indexes', m007_message_indexes),
    (8, 'groups', m008_groups),
    (9, 'e2ee v2 message uuid', m009_e2ee_v2_message_uuid),
    (10, 'resume tokens and session epoch', m010_resume_tokens),
    (11, 'contacts', m011_contacts),
    (12, 'usernames unique without case', m012_username_case),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def current_version(room, db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    row = db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    value = row['v'] if row is not None else None
    return int(value or 0)


def migrate(room):
    """Apply every migration newer than the database, each in its own
    transaction, recording it as it completes. Returns the versions applied."""
    with room._connect() as db:
        start = current_version(room, db)
    applied = []
    for version, name, step in MIGRATIONS:
        if version <= start:
            continue
        with room._connect() as db:
            step(room, db)
            db.execute(
                "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utcnow().isoformat()),
            )
        applied.append(version)
    return applied
