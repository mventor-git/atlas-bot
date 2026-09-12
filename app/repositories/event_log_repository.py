"""
Repository for event log entries (audit trail). (NEW v2.0)

Records every significant action for accountability and debugging.
Each entry stores timestamp, user, action, affected object,
and old/new values for complete auditability.
"""

from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.database import driver
from app.models.database import EventLogEntry
from app.repositories.base import BaseRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


# Standard event action constants
EVENT_REPORT_CREATED = "report.created"
EVENT_DRAFT_SAVED = "draft.saved"
EVENT_CONTRACTOR_ADDED = "contractor.added"
EVENT_CONTRACTOR_REMOVED = "contractor.removed"
EVENT_WORKERS_CHANGED = "workers.changed"
EVENT_ZONE_CHANGED = "zone.changed"
EVENT_DETAILS_CHANGED = "details.changed"
EVENT_PDF_GENERATED = "pdf.generated"
EVENT_REPORT_DELETED = "report.deleted"
EVENT_REPORT_FINALIZED = "report.finalized"
EVENT_REPORT_LOCKED = "report.locked"
EVENT_REPORT_UNLOCKED = "report.unlocked"
EVENT_REPORT_APPROVED = "report.approved"
EVENT_REPORT_REJECTED = "report.rejected"
EVENT_REPORT_RESUBMITTED = "report.resubmitted"
EVENT_VERSION_CREATED = "version.created"
EVENT_VERSION_RESTORED = "version.restored"
EVENT_FAVORITE_ADDED = "favorite.added"
EVENT_FAVORITE_REMOVED = "favorite.removed"


class EventLogRepository(BaseRepository[EventLogEntry]):
    """Repository for event log entries (audit trail).

    Records every significant action with full context for
    accountability, debugging, and auditing purposes.

    Usage:
        repo = EventLogRepository(db_manager)
        entry = repo.log("user1", "report.created", object_type="report", object_id=1)
        events = repo.get_by_user("user1")
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the repository.

        Args:
            db_manager: The database manager instance.
        """
        self._db = db_manager

    # --- High-level logging API ---

    def log(
        self,
        telegram_user: str,
        action: str,
        object_type: Optional[str] = None,
        object_id: Optional[int] = None,
        object_date: Optional[str] = None,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> EventLogEntry:
        """Log an event with automatic timestamp.

        Args:
            telegram_user: Who performed the action.
            action: Action identifier (use EVENT_* constants).
            object_type: Type of affected object.
            object_id: ID of affected object.
            object_date: Date of affected report.
            old_value: Previous state (JSON or string).
            new_value: New state (JSON or string).
            timestamp: Override timestamp (for testing). Uses current time if None.

        Returns:
            The created EventLogEntry with assigned ID.
        """
        entry = EventLogEntry(
            timestamp=timestamp or datetime.now().isoformat(),
            telegram_user=telegram_user,
            action=action,
            object_type=object_type,
            object_id=object_id,
            object_date=object_date,
            old_value=old_value,
            new_value=new_value,
        )
        result = self.add(entry)
        logger.debug(
            "Event logged: user=%s, action=%s, object=%s/%s",
            telegram_user, action, object_type, object_id,
        )
        return result

    # --- Query methods ---

    def get_by_user(self, telegram_user: str, limit: int = 50, site_id: str | None = None) -> list[EventLogEntry]:
        """Get events for a specific user, most recent first.

        Args:
            telegram_user: The Telegram user identifier.
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        rows = self._db.execute(
            """SELECT * FROM event_log
               WHERE telegram_user = ? AND site_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (telegram_user, site_id or driver.site_id(), limit),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_by_action(self, action: str, limit: int = 50, site_id: str | None = None) -> list[EventLogEntry]:
        """Get events of a specific type, most recent first.

        Args:
            action: Action identifier to filter by.
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        rows = self._db.execute(
            """SELECT * FROM event_log
               WHERE action = ? AND site_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (action, site_id or driver.site_id(), limit),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_by_object(
        self, object_type: str, object_id: int, limit: int = 50, site_id: str | None = None
    ) -> list[EventLogEntry]:
        """Get events for a specific object, most recent first.

        Args:
            object_type: Type of object (e.g., 'report').
            object_id: ID of the object.
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        rows = self._db.execute(
            """SELECT * FROM event_log
               WHERE object_type = ? AND object_id = ? AND site_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (object_type, object_id, site_id or driver.site_id(), limit),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_recent(self, limit: int = 50, site_id: str | None = None) -> list[EventLogEntry]:
        """Get the most recent events across all users.

        Args:
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        rows = self._db.execute(
            "SELECT * FROM event_log WHERE site_id = ? ORDER BY timestamp DESC LIMIT ?",
            (site_id or driver.site_id(), limit),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_by_date_range(
        self, start_date: str, end_date: str, limit: int = 100, site_id: str | None = None
    ) -> list[EventLogEntry]:
        """Get events within a date range.

        Args:
            start_date: Start date (YYYY-MM-DD) or ISO datetime.
            end_date: End date (YYYY-MM-DD) or ISO datetime.
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        rows = self._db.execute(
            """SELECT * FROM event_log
               WHERE timestamp >= ? AND timestamp <= ? AND site_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (start_date, end_date, site_id or driver.site_id(), limit),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def cleanup_old_events(self, days: int = 90) -> int:
        """Delete events older than N days.

        Args:
            days: Delete events older than this many days.

        Returns:
            Number of events deleted.
        """
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self._db.execute(
            "DELETE FROM event_log WHERE timestamp < ? AND site_id = ?",
            (cutoff, driver.site_id()),
        )
        self._db.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("Cleaned up %d events older than %d days", deleted, days)
        return deleted

    def count(self, site_id: str | None = None) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM event_log WHERE site_id = ?",
            (site_id or driver.site_id(),),
        ).fetchone()
        return row["cnt"] if row else 0

    # --- BaseRepository implementation ---

    def get_by_id(self, entity_id: int, site_id: str | None = None) -> Optional[EventLogEntry]:
        row = self._db.execute(
            "SELECT * FROM event_log WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self, site_id: str | None = None) -> list[EventLogEntry]:
        rows = self._db.execute(
            "SELECT * FROM event_log WHERE site_id = ? ORDER BY timestamp DESC",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def add(self, entity: EventLogEntry) -> EventLogEntry:
        site = entity.site_id or driver.site_id()
        entity.site_id = site
        cursor = self._db.execute(
            """INSERT INTO event_log
               (timestamp, telegram_user, site_id, action, object_type, object_id,
                object_date, old_value, new_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.timestamp,
                entity.telegram_user,
                site,
                entity.action,
                entity.object_type,
                entity.object_id,
                entity.object_date,
                entity.old_value,
                entity.new_value,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def update(self, entity: EventLogEntry) -> EventLogEntry:
        raise DatabaseError("Event log entries are immutable — cannot update.")

    def delete(self, entity_id: int, site_id: str | None = None) -> bool:
        cursor = self._db.execute(
            "DELETE FROM event_log WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        )
        self._db.commit()
        return cursor.rowcount > 0

    # --- Private ---

    @staticmethod
    def _row_to_model(row) -> EventLogEntry:
        return EventLogEntry(
            id=row["id"],
            timestamp=row["timestamp"],
            telegram_user=row["telegram_user"],
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            action=row["action"],
            object_type=row["object_type"],
            object_id=row["object_id"],
            object_date=row["object_date"],
            old_value=row["old_value"],
            new_value=row["new_value"],
        )
