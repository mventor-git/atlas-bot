"""Attendance day repository (026): site-scoped day aggregates + claims."""

from __future__ import annotations

from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.attendance import AttendanceClaim, AttendanceDay
from app.utils.logger import get_logger

logger = get_logger(__name__)

_DAY_COLUMNS = (
    "chat_id, site_id, day_date, status, origin,"
    " first_in, last_out, late_minutes, verdict,"
    " resolution_note, resolved_by, dispute_note,"
    " created_at, resolved_at"
)

_CLAIM_COLUMNS = (
    "chat_id, day_date, site_id, kind, text, raised_by,"
    " status, decided_by, decision_note, created_at, decided_at"
)


class AttendanceDayRepository:
    """Persistence for attendance_days + attendance_claims.

    Days are never deleted: resolution history lives here for audits.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    # --- days ---

    def add_day(self, entity: AttendanceDay) -> AttendanceDay:
        site = entity.site_id or driver.site_id()
        entity.site_id = site
        cursor = self._db.execute(
            f"""INSERT INTO attendance_days ({_DAY_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.chat_id, site, entity.day_date, entity.status,
                entity.origin, entity.first_in, entity.last_out,
                entity.late_minutes, entity.verdict, entity.resolution_note,
                entity.resolved_by, entity.dispute_note,
                entity.created_at, entity.resolved_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_day(self, chat_id: str, day_date: str,
                site_id: str | None = None) -> Optional[AttendanceDay]:
        row = self._db.execute(
            """SELECT * FROM attendance_days
               WHERE chat_id = ? AND day_date = ? AND site_id = ?""",
            (chat_id, day_date, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_day(row) if row else None

    def get_day_by_id(self, day_id: int,
                      site_id: str | None = None) -> Optional[AttendanceDay]:
        row = self._db.execute(
            "SELECT * FROM attendance_days WHERE id = ? AND site_id = ?",
            (day_id, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_day(row) if row else None

    def days_for_site_day(self, day_date: str,
                          site_id: str | None = None) -> list[AttendanceDay]:
        """All day rows for a site+date (created by sweep or events)."""
        rows = self._db.execute(
            """SELECT * FROM attendance_days
               WHERE day_date = ? AND site_id = ?
               ORDER BY chat_id ASC""",
            (day_date, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_day(r) for r in rows]

    def days_for_chat(self, chat_id: str,
                      site_id: str | None = None) -> list[AttendanceDay]:
        rows = self._db.execute(
            """SELECT * FROM attendance_days
               WHERE chat_id = ? AND site_id = ?
               ORDER BY day_date DESC""",
            (chat_id, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_day(r) for r in rows]

    def update_day(self, entity: AttendanceDay) -> AttendanceDay:
        if entity.id is None:
            raise ValueError("Cannot update a day without an ID.")
        from datetime import datetime

        cursor = self._db.execute(
            """UPDATE attendance_days
               SET status=?, origin=?, first_in=?, last_out=?,
                   late_minutes=?, verdict=?, resolution_note=?,
                   resolved_by=?, dispute_note=?, resolved_at=?
               WHERE id=? AND site_id=?""",
            (
                entity.status, entity.origin, entity.first_in,
                entity.last_out, entity.late_minutes, entity.verdict,
                entity.resolution_note, entity.resolved_by,
                entity.dispute_note,
                entity.resolved_at or datetime.now().isoformat(),
                entity.id, entity.site_id or driver.site_id(),
            ),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Attendance day {entity.id} not found in this site.")
        entity.resolved_at = entity.resolved_at or datetime.now().isoformat()
        return entity

    # --- claims ---

    def add_claim(self, entity: AttendanceClaim) -> AttendanceClaim:
        site = entity.site_id or driver.site_id()
        entity.site_id = site
        cursor = self._db.execute(
            f"""INSERT INTO attendance_claims ({_CLAIM_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.chat_id, entity.day_date, site, entity.kind,
                entity.text, entity.raised_by, entity.status,
                entity.decided_by, entity.decision_note,
                entity.created_at, entity.decided_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_claim(self, claim_id: int,
                  site_id: str | None = None) -> Optional[AttendanceClaim]:
        row = self._db.execute(
            "SELECT * FROM attendance_claims WHERE id = ? AND site_id = ?",
            (claim_id, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_claim(row) if row else None

    def claims_for_day(self, chat_id: str, day_date: str,
                       site_id: str | None = None) -> list[AttendanceClaim]:
        rows = self._db.execute(
            """SELECT * FROM attendance_claims
               WHERE chat_id = ? AND day_date = ? AND site_id = ?
               ORDER BY created_at ASC""",
            (chat_id, day_date, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    def open_claims(self, site_id: str | None = None) -> list[AttendanceClaim]:
        rows = self._db.execute(
            """SELECT * FROM attendance_claims
               WHERE site_id = ? AND status = 'open'
               ORDER BY created_at ASC""",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    def update_claim(self, entity: AttendanceClaim) -> AttendanceClaim:
        if entity.id is None:
            raise ValueError("Cannot update a claim without an ID.")
        from datetime import datetime

        cursor = self._db.execute(
            """UPDATE attendance_claims
               SET status=?, decided_by=?, decision_note=?, decided_at=?
               WHERE id=? AND site_id=?""",
            (
                entity.status, entity.decided_by, entity.decision_note,
                entity.decided_at or datetime.now().isoformat(),
                entity.id, entity.site_id or driver.site_id(),
            ),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Attendance claim {entity.id} not found in this site.")
        entity.decided_at = entity.decided_at or datetime.now().isoformat()
        return entity

    # --- mapping ---

    @staticmethod
    def _row_to_day(row) -> AttendanceDay:
        return AttendanceDay(
            id=row["id"],
            chat_id=str(row["chat_id"]),
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            day_date=row["day_date"],
            status=row["status"],
            origin=row["origin"],
            first_in=row["first_in"],
            last_out=row["last_out"],
            late_minutes=row["late_minutes"],
            verdict=row["verdict"],
            resolution_note=row["resolution_note"],
            resolved_by=row["resolved_by"],
            dispute_note=row["dispute_note"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    @staticmethod
    def _row_to_claim(row) -> AttendanceClaim:
        return AttendanceClaim(
            id=row["id"],
            chat_id=str(row["chat_id"]),
            day_date=row["day_date"],
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            kind=row["kind"],
            text=row["text"],
            raised_by=str(row["raised_by"]),
            status=row["status"],
            decided_by=row["decided_by"],
            decision_note=row["decision_note"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
        )
