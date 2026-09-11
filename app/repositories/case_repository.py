"""Case repository (014): site-scoped case persistence."""

from __future__ import annotations

from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.case import Case
from app.repositories.base import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

_COLUMNS = (
    "reporter_chat_id, site_id, case_type, summary,"
    " status, reviewed_by, resolved_by,"
    " resolution_note, appeal_note, created_at, resolved_at"
)


class CaseRepository(BaseRepository[Case]):
    """Persistence for case_events (tenant-scoped reads/writes)."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def add(self, entity: Case) -> Case:
        site = entity.site_id or driver.site_id()
        entity.site_id = site
        cursor = self._db.execute(
            f"""INSERT INTO case_events ({_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.reporter_chat_id, site, entity.case_type,
                entity.summary, entity.status, entity.reviewed_by,
                entity.resolved_by, entity.resolution_note,
                entity.appeal_note, entity.created_at, entity.resolved_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_by_id(self, entity_id: int, site_id: str | None = None) -> Optional[Case]:
        row = self._db.execute(
            "SELECT * FROM case_events WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self, site_id: str | None = None) -> list[Case]:
        rows = self._db.execute(
            "SELECT * FROM case_events WHERE site_id = ? ORDER BY created_at DESC",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def for_reporter(self, chat_id: str, site_id: str | None = None) -> list[Case]:
        """All cases filed by one user (own-history view)."""
        rows = self._db.execute(
            """SELECT * FROM case_events
               WHERE reporter_chat_id = ? AND site_id = ?
               ORDER BY created_at ASC""",
            (chat_id, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def open_cases(self, site_id: str | None = None) -> list[Case]:
        """Reviewer queue: everything not yet resolved."""
        rows = self._db.execute(
            """SELECT * FROM case_events
               WHERE site_id = ? AND status != 'resolved'
               ORDER BY created_at ASC""",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def update(self, entity: Case) -> Case:
        if entity.id is None:
            raise ValueError("Cannot update a case without an ID.")
        from datetime import datetime

        cursor = self._db.execute(
            """UPDATE case_events
               SET status=?, reviewed_by=?, resolved_by=?,
                   resolution_note=?, appeal_note=?, resolved_at=?
               WHERE id=? AND site_id=?""",
            (
                entity.status, entity.reviewed_by, entity.resolved_by,
                entity.resolution_note, entity.appeal_note,
                entity.resolved_at or datetime.now().isoformat(),
                entity.id, entity.site_id or driver.site_id(),
            ),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Case {entity.id} not found in this site.")
        entity.resolved_at = entity.resolved_at or datetime.now().isoformat()
        return entity

    def delete(self, entity_id: int, site_id: str | None = None) -> bool:
        cursor = self._db.execute(
            "DELETE FROM case_events WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self, site_id: str | None = None) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM case_events WHERE site_id = ?",
            (site_id or driver.site_id(),),
        ).fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_model(row) -> Case:
        return Case(
            id=row["id"],
            reporter_chat_id=str(row["reporter_chat_id"]),
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            case_type=row["case_type"],
            summary=row["summary"],
            status=row["status"],
            reviewed_by=row["reviewed_by"],
            resolved_by=row["resolved_by"],
            resolution_note=row["resolution_note"],
            appeal_note=row["appeal_note"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )
