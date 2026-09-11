"""Attendance repository (013): site-scoped event persistence."""

from __future__ import annotations

from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.attendance import AttendanceEvent
from app.repositories.base import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

_COLUMNS = (
    "chat_id, site_id, event_date, check_type, method,"
    " latitude, longitude, accuracy_m, location_verdict,"
    " assisted_target_chat_id, assisted_reason, initiated_by,"
    " status, verdict, confirmed_by, late_minutes, note,"
    " created_at, resolved_at"
)


class AttendanceRepository(BaseRepository[AttendanceEvent]):
    """Persistence for attendance_events (tenant-scoped reads/writes)."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def add(self, entity: AttendanceEvent) -> AttendanceEvent:
        site = entity.site_id or driver.site_id()
        entity.site_id = site
        cursor = self._db.execute(
            f"""INSERT INTO attendance_events ({_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.chat_id, site, entity.event_date, entity.check_type,
                entity.method, entity.latitude, entity.longitude,
                entity.accuracy_m, entity.location_verdict,
                entity.assisted_target_chat_id, entity.assisted_reason,
                entity.initiated_by, entity.status, entity.verdict,
                entity.confirmed_by, entity.late_minutes, entity.note,
                entity.created_at, entity.resolved_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_by_id(self, entity_id: int, site_id: str | None = None) -> Optional[AttendanceEvent]:
        row = self._db.execute(
            "SELECT * FROM attendance_events WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self, site_id: str | None = None) -> list[AttendanceEvent]:
        rows = self._db.execute(
            "SELECT * FROM attendance_events WHERE site_id = ? ORDER BY created_at DESC",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def for_user_day(self, chat_id: str, event_date: str,
                     site_id: str | None = None) -> list[AttendanceEvent]:
        """All attempts of a user on a date (duplicates visible, not hidden)."""
        rows = self._db.execute(
            """SELECT * FROM attendance_events
               WHERE chat_id = ? AND event_date = ? AND site_id = ?
               ORDER BY created_at ASC""",
            (chat_id, event_date, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def for_site_day(self, event_date: str,
                     site_id: str | None = None) -> list[AttendanceEvent]:
        rows = self._db.execute(
            """SELECT * FROM attendance_events
               WHERE event_date = ? AND site_id = ?
               ORDER BY created_at ASC""",
            (event_date, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def update(self, entity: AttendanceEvent) -> AttendanceEvent:
        if entity.id is None:
            raise ValueError("Cannot update an event without an ID.")
        from datetime import datetime

        cursor = self._db.execute(
            """UPDATE attendance_events
               SET status=?, verdict=?, confirmed_by=?, late_minutes=?,
                   note=?, resolved_at=?
               WHERE id=? AND site_id=?""",
            (
                entity.status, entity.verdict, entity.confirmed_by,
                entity.late_minutes, entity.note,
                entity.resolved_at or datetime.now().isoformat(),
                entity.id, entity.site_id or driver.site_id(),
            ),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Attendance event {entity.id} not found in this site.")
        entity.resolved_at = entity.resolved_at or datetime.now().isoformat()
        return entity

    def delete(self, entity_id: int, site_id: str | None = None) -> bool:
        cursor = self._db.execute(
            "DELETE FROM attendance_events WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self, site_id: str | None = None) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM attendance_events WHERE site_id = ?",
            (site_id or driver.site_id(),),
        ).fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_model(row) -> AttendanceEvent:
        return AttendanceEvent(
            id=row["id"],
            chat_id=str(row["chat_id"]),
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            event_date=row["event_date"],
            check_type=row["check_type"],
            method=row["method"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            accuracy_m=row["accuracy_m"],
            location_verdict=row["location_verdict"],
            assisted_target_chat_id=row["assisted_target_chat_id"],
            assisted_reason=row["assisted_reason"],
            initiated_by=row["initiated_by"],
            status=row["status"],
            verdict=row["verdict"],
            confirmed_by=row["confirmed_by"],
            late_minutes=row["late_minutes"],
            note=row["note"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )
