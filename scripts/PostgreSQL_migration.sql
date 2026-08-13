-- PostgreSQL migration script for Reveal-X
-- This script adds the new columns for share expiration and one-time access
-- Assumes the base tables already exist from the original SQLite version

-- Add expiration and share access tracking to messages table
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS expires_at TEXT,
ADD COLUMN IF NOT EXISTS share1_accessed BOOLEAN DEFAULT FALSE;

-- Add last_seen, profile_image and the E2EE public key to users if missing
ALTER TABLE users
ADD COLUMN IF NOT EXISTS last_seen TEXT,
ADD COLUMN IF NOT EXISTS profile_image TEXT,
ADD COLUMN IF NOT EXISTS public_key TEXT;

-- Add read_at to messages table if missing
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS read_at TEXT;

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id);
CREATE INDEX IF NOT EXISTS idx_messages_expires ON messages(expires_at);

-- Note: The password hash will be updated automatically when users next log in
-- due to the verification fallback in the model.