"""Web authentication (ticket-037): local credentials bridged to Atlas identity.

- Login: username + password (werkzeug scrypt hash). No default accounts;
  first account via CLI bootstrap linked to the SUPERADMIN chat_id.
- Identity: web_users.chat_id IS the Atlas subject. Every authorization
  call downstream uses chat_id against AuthorizationService — the same
  subject as Telegram, never a parallel account.
- Sessions: opaque id in HttpOnly SameSite=Lax cookie; state server-side
  in web_sessions with expiry. Logout deletes the row.
- CSRF: per-session token, required on all POST (header or form field).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

SESSION_TTL_HOURS = 12
SESSION_COOKIE = "atlas_web_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# --- web_users -------------------------------------------------------------

def find_web_user(db, username: str):
    row = db.execute(
        "SELECT * FROM web_users WHERE username = ?", (username.strip(),)
    ).fetchone()
    return dict(row) if row else None


def get_web_user_by_chat(db, chat_id: str):
    row = db.execute(
        "SELECT * FROM web_users WHERE chat_id = ?", (str(chat_id),)
    ).fetchone()
    return dict(row) if row else None


def list_web_users(db):
    rows = db.execute(
        "SELECT id, username, chat_id, created_by, created_at, disabled"
        " FROM web_users ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def create_web_user(db, username: str, password: str, chat_id: str,
                    created_by: str | None) -> dict:
    """Link a web login to an existing Atlas user row. Validates nothing
    about Atlas state here — callers must check user row + superadmin."""
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("bad username")
    if len(password) < 10:
        raise ValueError("password must be 10+ chars")
    if find_web_user(db, username) or get_web_user_by_chat(db, chat_id):
        raise ValueError("username or identity already linked")
    db.execute(
        "INSERT INTO web_users (username, password_hash, chat_id, created_by)"
        " VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), str(chat_id), created_by),
    )
    db.commit()
    return find_web_user(db, username)


def set_web_user_disabled(db, username: str, disabled: bool) -> None:
    db.execute("UPDATE web_users SET disabled = ? WHERE username = ?",
               (1 if disabled else 0, username))
    db.commit()


def verify_password(db, username: str, password: str):
    """Return web_user dict on success, None otherwise (incl. disabled)."""
    user = find_web_user(db, username)
    if not user or user.get("disabled"):
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


# --- sessions --------------------------------------------------------------

def create_session(db, chat_id: str) -> tuple[str, str]:
    """Return (session_id, csrf). Prunes expired rows opportunistically."""
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    exp = _iso(_now() + timedelta(hours=SESSION_TTL_HOURS))
    db.execute("DELETE FROM web_sessions WHERE expires_at <= ?",
               (_iso(_now()),))
    db.execute(
        "INSERT INTO web_sessions (id, chat_id, csrf, expires_at)"
        " VALUES (?, ?, ?, ?)",
        (sid, str(chat_id), csrf, exp),
    )
    db.commit()
    return sid, csrf


def get_session(db, sid: str | None):
    if not sid:
        return None
    row = db.execute(
        "SELECT * FROM web_sessions WHERE id = ?", (sid,)
    ).fetchone()
    if not row:
        return None
    sess = dict(row)
    if sess["expires_at"] <= _iso(_now()):
        db.execute("DELETE FROM web_sessions WHERE id = ?", (sid,))
        db.commit()
        return None
    return sess


def destroy_session(db, sid: str | None) -> None:
    if sid:
        db.execute("DELETE FROM web_sessions WHERE id = ?", (sid,))
        db.commit()


def check_csrf(session: dict, provided: str | None) -> bool:
    return bool(provided) and secrets.compare_digest(session["csrf"], provided)
