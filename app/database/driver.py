"""Backend driver selection: SQLite default, Postgres on DATABASE_URL.

All repository SQL uses ``?`` placeholders. On Postgres they are
translated to ``%s`` at execution time, so no SQL string needs two
versions. ``INSERT`` statements gain ``RETURNING id`` automatically on
Postgres, keeping ``cursor.lastrowid`` working on both backends.
"""

from __future__ import annotations

import os


def database_url() -> str:
    """Postgres URL from the environment (empty = SQLite mode)."""
    return os.environ.get("DATABASE_URL", "").strip()


def use_postgres() -> bool:
    """True when DATABASE_URL points at Postgres."""
    return database_url().startswith(("postgres://", "postgresql://"))


def site_id() -> str:
    """Site identity for this bot instance (fleet-ready)."""
    return os.environ.get("SITE_ID", "default").strip() or "default"


def translate(sql: str, pg: bool) -> str:
    """Translate ``?`` placeholders to ``%s`` for Postgres."""
    if not pg:
        return sql
    return sql.replace("?", "%s")


def needs_returning(sql: str, pg: bool) -> bool:
    """True when Postgres needs an appended ``RETURNING id``."""
    if not pg:
        return False
    head = sql.lstrip().upper()
    return head.startswith("INSERT") and "RETURNING" not in head.upper()


def ddl_fixups(sql: str) -> str:
    """Translate SQLite-only DDL tokens to Postgres equivalents.

    Only touches tokens unique to DDL (never present in DML):
    AUTOINCREMENT, datetime('now') defaults, COLLATE NOCASE.
    """
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    sql = sql.replace("datetime('now')", "now()")
    sql = sql.replace(" COLLATE NOCASE", "")
    return sql


class PgCursor:
    """Wraps a psycopg2 cursor with a ``lastrowid`` property.

    After an auto-appended ``RETURNING id``, the first ``lastrowid``
    access fetches the generated id. All other attributes delegate
    to the wrapped cursor (fetchone/fetchall/rowcount/...).
    """

    def __init__(self, cursor) -> None:
        self._cursor = cursor
        self._lastrowid = None
        self._resolved = False

    @property
    def lastrowid(self):
        if not self._resolved:
            self._resolved = True
            try:
                row = self._cursor.fetchone()
                # RealDictRow has no positional index - read first value.
                self._lastrowid = list(row.values())[0] if row else None
            except Exception:
                self._lastrowid = None
        return self._lastrowid

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)
