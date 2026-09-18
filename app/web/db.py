"""HQ Web App tables (ticket-037). Additive only; existing schema untouched.

web_users: local web credential linked to exactly one Atlas user row
  (users.chat_id). One Atlas identity, never a duplicate account.
web_sessions: opaque server-side sessions; cookie holds only the id.
"""
from __future__ import annotations

WEB_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS web_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  chat_id TEXT NOT NULL UNIQUE,
  created_by TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  disabled INTEGER NOT NULL DEFAULT 0,
  manual_ack INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS web_sessions (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  csrf TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);
"""

WEB_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS web_users (
  id SERIAL PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  chat_id TEXT NOT NULL UNIQUE,
  created_by TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  disabled INTEGER NOT NULL DEFAULT 0,
  manual_ack INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS web_sessions (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  csrf TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);
"""

_MANUAL_ACK_SQLITE = "ALTER TABLE web_users ADD COLUMN manual_ack INTEGER NOT NULL DEFAULT 0"
_MANUAL_ACK_PG = "ALTER TABLE web_users ADD COLUMN IF NOT EXISTS manual_ack INTEGER NOT NULL DEFAULT 0"


def ensure_web_tables(db_manager) -> None:
    """Create web tables if missing (SQLite + Postgres)."""
    from app.database import driver

    schema = WEB_SCHEMA_PG if driver.use_postgres() else WEB_SCHEMA_SQLITE
    stmts = [s.strip() for s in schema.split(";") if s.strip()]
    for stmt in stmts:
        # No placeholders in DDL; pass through (PG flag kept for clarity).
        db_manager.execute(stmt)
    try:
        db_manager.execute(_MANUAL_ACK_PG if driver.use_postgres()
                           else _MANUAL_ACK_SQLITE)
    except Exception:
        pass  # column already present (SQLite has no IF NOT EXISTS path)
    db_manager.commit()
