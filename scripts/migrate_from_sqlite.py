#!/usr/bin/env python3
"""
[NEW] Migrate Reveal-X data from SQLite to PostgreSQL.

This script handles:
1. Exporting SQLite data to JSON for backup
2. Creating PostgreSQL schema if not already present
3. Importing data with proper type conversions

Usage:
    python migrate_from_sqlite.py --sqlite app/data/reveal_x.db --postgres postgresql://user:pass@host:5432/db

"""

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False


def export_sqlite_to_json(sqlite_path, output_path):
    """Export SQLite database to JSON for backup."""
    print(f"[*] Exporting SQLite data from {sqlite_path}...")
    
    if not os.path.isfile(sqlite_path):
        print(f"[!] SQLite database not found: {sqlite_path}")
        return None
    
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    data = {}
    
    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    
    for table in tables:
        cursor.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        data[table] = [dict(row) for row in rows]
        print(f"  [+] Exported {table}: {len(rows)} rows")
    
    conn.close()
    
    # Write to JSON file
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    
    print(f"[+] Backup saved to {output_path}")
    return data


def create_postgres_schema(db_url):
    """Create PostgreSQL schema if tables don't exist."""
    print("[*] Creating PostgreSQL schema...")
    
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    
    try:
        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                profile_image TEXT,
                last_seen TEXT,
                created_at TEXT NOT NULL
            )
        """)
        print("  [+] Created users table")
        
        # Create messages table
        cursor.execute("""
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
        print("  [+] Created messages table")
        
        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_expires ON messages(expires_at)")
        print("  [+] Created indexes")
        
        conn.commit()
        print("[+] PostgreSQL schema created successfully")
        
    except Exception as e:
        print(f"[!] Error creating schema: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def import_data_to_postgres(db_url, data):
    """Import JSON data to PostgreSQL."""
    print("[*] Importing data to PostgreSQL...")
    
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    
    try:
        # Clear existing data (optional - comment out to preserve existing data)
        # cursor.execute("TRUNCATE users CASCADE")
        # cursor.execute("TRUNCATE messages CASCADE")
        
        # Import users
        if 'users' in data:
            for user in data['users']:
                cursor.execute(
                    """
                    INSERT INTO users (id, username, password_hash, profile_image, last_seen, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        profile_image = EXCLUDED.profile_image,
                        last_seen = EXCLUDED.last_seen
                    """,
                    (
                        user.get('id'),
                        user.get('username'),
                        user.get('password_hash'),
                        user.get('profile_image'),
                        user.get('last_seen'),
                        user.get('created_at')
                    )
                )
            print(f"  [+] Imported {len(data['users'])} users")
        
        # Import messages
        if 'messages' in data:
            for msg in data['messages']:
                # Ensure extra is valid JSON
                extra = msg.get('extra', '{}')
                if isinstance(extra, dict):
                    extra = json.dumps(extra)
                
                cursor.execute(
                    """
                    INSERT INTO messages (id, sender_id, recipient_id, type, content, extra, read_at, created_at, expires_at, share1_accessed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        read_at = EXCLUDED.read_at,
                        share1_accessed = EXCLUDED.share1_accessed
                    """,
                    (
                        msg.get('id'),
                        msg.get('sender_id'),
                        msg.get('recipient_id'),
                        msg.get('type'),
                        msg.get('content'),
                        extra,
                        msg.get('read_at'),
                        msg.get('created_at'),
                        msg.get('expires_at'),
                        msg.get('share1_accessed', False)
                    )
                )
            print(f"  [+] Imported {len(data['messages'])} messages")
        
        conn.commit()
        print("[+] Data imported successfully")
        
    except Exception as e:
        print(f"[!] Error importing data: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Migrate Reveal-X from SQLite to PostgreSQL')
    parser.add_argument('--sqlite', required=True, help='Path to SQLite database')
    parser.add_argument('--postgres', required=True, help='PostgreSQL connection URL')
    parser.add_argument('--backup', default='reveal_x_backup.json', help='Output file for backup JSON')
    parser.add_argument('--skip-backup', action='store_true', help='Skip creating JSON backup')
    
    args = parser.parse_args()
    
    if not POSTGRES_AVAILABLE:
        print("[!] psycopg2 not installed. Install with: pip install psycopg2-binary")
        return 1
    
    print("[*] Reveal-X Migration Tool")
    print(f"[*] SQLite: {args.sqlite}")
    print(f"[*] PostgreSQL: {args.postgres}")
    print()
    
    # Step 1: Backup SQLite data
    if not args.skip_backup:
        data = export_sqlite_to_json(args.sqlite, args.backup)
        if not data:
            return 1
    else:
        # Still need to load the data
        conn = sqlite3.connect(args.sqlite)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        data = {}
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for (table,) in cursor.fetchall():
            cursor.execute(f"SELECT * FROM {table}")
            data[table] = [dict(row) for row in cursor.fetchall()]
        conn.close()
    
    print()
    
    # Step 2: Create PostgreSQL schema
    try:
        create_postgres_schema(args.postgres)
    except Exception as e:
        print(f"[!] Failed to create schema: {e}")
        return 1
    
    print()
    
    # Step 3: Import data
    try:
        import_data_to_postgres(args.postgres, data)
    except Exception as e:
        print(f"[!] Failed to import data: {e}")
        return 1
    
    print()
    print("[+] Migration completed successfully!")
    print("[*] Next steps:")
    print("    1. Update your .env file with DATABASE_URL=postgresql://...")
    print("    2. Test the application with: python run.py")
    print("    3. Backup your original SQLite database")
    print("    4. Delete SQLite database if migration is confirmed working")
    
    return 0


if __name__ == '__main__':
    exit(main())
