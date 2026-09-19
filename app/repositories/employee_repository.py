"""Employee directory: chat_id -> (employee_code, full_name).

Single seam for identity display: every queue card and audit line uses
``CODE · Name`` from this table — never the Telegram display name, never
the raw chat ID (extends the P0 mask rule). Unknown chats resolve to
``UNMAPPED · limited``: the request still files, with guidance to register.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.utils.exceptions import DatabaseError

UNKNOWN_CODE = "UNMAPPED"
UNKNOWN_NAME = "limited"

REGISTER_HINT = (
    "Unmapped account — send /register to file a registration request."
)


@dataclass
class Employee:
    """One directory row: Telegram chat mapped to a human + code."""

    chat_id: str
    employee_code: str
    full_name: str
    status: str = "active"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: Optional[str] = None
    id: Optional[int] = None

    @property
    def active(self) -> bool:
        return self.status == "active"


def format_employee(code: str, name: str) -> str:
    """Display form used on every card: ``CODE · Name`` (no chat digits)."""
    return f"{code} · {name}"


class EmployeeRepository:
    """Persistence for the employees directory (chat_id keyed)."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def get(self, chat_id: str) -> Optional[Employee]:
        row = self._db.execute(
            "SELECT * FROM employees WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def resolve(self, chat_id: str) -> tuple[str, str]:
        """(code, name) for an ACTIVE chat; else (UNMAPPED, limited)."""
        emp = self.get(chat_id)
        if emp is None or not emp.active:
            return UNKNOWN_CODE, UNKNOWN_NAME
        return emp.employee_code, emp.full_name

    def display(self, chat_id: str) -> str:
        """Card-ready ``CODE · Name`` (or UNMAPPED form + register hint)."""
        code, name = self.resolve(chat_id)
        if code == UNKNOWN_CODE:
            return f"{format_employee(code, name)} — {REGISTER_HINT}"
        return format_employee(code, name)

    def register(self, chat_id: str, code: str, full_name: str) -> Employee:
        """Upsert a directory row (admin path only; service is auth-free).

        Re-hire reactivates the same chat row with a NEW code; the retired
        code survives only in the audit trail (chat_id/code stay UNIQUE).
        """
        chat_id, code, full_name = (
            str(chat_id).strip(), str(code).strip(), str(full_name).strip())
        if not chat_id or not code or not full_name:
            raise ValueError("chat_id, CODE and full name are all required.")
        now = datetime.now().isoformat()
        existing = self.get(chat_id)
        if existing is None:
            cursor = self._db.execute(
                """INSERT INTO employees (chat_id, employee_code, full_name,
                    status, created_at, updated_at)
                    VALUES (?, ?, ?, 'active', ?, ?)""",
                (chat_id, code, full_name, now, now),
            )
            self._db.commit()
            return Employee(chat_id=chat_id, employee_code=code,
                            full_name=full_name, created_at=now,
                            updated_at=now, id=cursor.lastrowid)
        self._db.execute(
            """UPDATE employees SET employee_code=?, full_name=?,
               status='active', updated_at=? WHERE chat_id=?""",
            (code, full_name, now, chat_id),
        )
        self._db.commit()
        existing.employee_code, existing.full_name = code, full_name
        existing.status, existing.updated_at = "active", now
        return existing

    def deactivate(self, chat_id: str) -> bool:
        """Deactivate a directory row (keeps history; resolve goes UNMAPPED)."""
        cursor = self._db.execute(
            """UPDATE employees SET status='deactivated', updated_at=?
               WHERE chat_id=? AND status='active'""",
            (datetime.now().isoformat(), str(chat_id)),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def issue_code(self, chat_id: str, full_name: str) -> Employee:
        """Register a new hire with an auto EMP-### code.

        Code rule: 1 + max numeric EMP-<digits> suffix (EMP-001 padded).
        UNIQUE-safe under concurrency: the DB constraint wins, retry next.
        """
        name = str(full_name or "").strip()
        if not str(chat_id).strip() or not name:
            raise ValueError("chat_id and full name are required.")
        if self.get(str(chat_id).strip()) is not None:
            raise ValueError("Chat is already registered.")
        for _ in range(25):  # ponytail: enough headroom, races are rare
            try:
                return self.register(str(chat_id).strip(),
                                     self.next_code(), name)
            except DatabaseError as e:
                if "UNIQUE" not in str(
                        getattr(e, "original_exception", e)).upper():
                    raise
        raise DatabaseError("Could not allocate an employee code, retry.")

    def next_code(self) -> str:
        """Next EMP-### candidate from the current max numeric suffix."""
        rows = self._db.execute(
            "SELECT employee_code FROM employees").fetchall()
        peak = 0
        for r in rows:
            code = str(r["employee_code"] or "").strip()
            if code.startswith("EMP-") and code[4:].isdigit():
                peak = max(peak, int(code[4:]))
        return f"EMP-{peak + 1:03d}"

    @staticmethod
    def _row_to_model(row) -> Employee:
        keys = row.keys()
        return Employee(
            id=row["id"],
            chat_id=row["chat_id"],
            employee_code=row["employee_code"],
            full_name=row["full_name"],
            status=row["status"] if "status" in keys else "active",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
