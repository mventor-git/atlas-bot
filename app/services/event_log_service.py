"""
Event Log Service â€” High-level audit trail facade. (mventor-ticket-023)

Provides convenience methods for logging common actions without
manually constructing EventLogEntry objects. Wraps EventLogRepository
with automatic timestamping and structured event construction.

Usage:
    service = EventLogService(db_manager)
    service.log_report_created("user1", report)  # Auto-logs with all context
"""

from datetime import datetime
from typing import Optional, Any

from app.database.manager import DatabaseManager
from app.models.database import EventLogEntry, Report
from app.repositories.event_log_repository import (
    EventLogRepository,
    EVENT_REPORT_CREATED,
    EVENT_REPORT_FINALIZED,
    EVENT_REPORT_DELETED,
    EVENT_DRAFT_SAVED,
    EVENT_CONTRACTOR_ADDED,
    EVENT_CONTRACTOR_REMOVED,
    EVENT_WORKERS_CHANGED,
    EVENT_ZONE_CHANGED,
    EVENT_DETAILS_CHANGED,
    EVENT_PDF_GENERATED,
    EVENT_REPORT_LOCKED,
    EVENT_REPORT_UNLOCKED,
    EVENT_VERSION_CREATED,
    EVENT_VERSION_RESTORED,
    EVENT_FAVORITE_ADDED,
    EVENT_FAVORITE_REMOVED,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class EventLogService:
    """High-level event logging facade.

    Provides convenient methods for logging common actions
    with proper context extraction from domain objects.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the service.

        Args:
            db_manager: The database manager instance.
        """
        self._repo = EventLogRepository(db_manager)

    # --- Direct log access ---

    def log(
        self,
        telegram_user: str,
        action: str,
        object_type: Optional[str] = None,
        object_id: Optional[int] = None,
        object_date: Optional[str] = None,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
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

        Returns:
            The created EventLogEntry with assigned ID.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=action,
            object_type=object_type,
            object_id=object_id,
            object_date=object_date,
            old_value=old_value,
            new_value=new_value,
        )

    # --- Convenience methods for Report lifecycle ---

    def log_report_created(
        self, telegram_user: str, report: Report
    ) -> EventLogEntry:
        """Log that a report was created.

        Args:
            telegram_user: Who created the report.
            report: The created report.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_REPORT_CREATED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            new_value=self._report_summary(report),
        )

    def log_draft_saved(
        self, telegram_user: str, report: Report,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
    ) -> EventLogEntry:
        """Log that a draft was saved.

        Args:
            telegram_user: Who saved the draft.
            report: The saved report.
            old_value: Previous state summary.
            new_value: New state summary.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_DRAFT_SAVED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            old_value=old_value,
            new_value=new_value or self._report_summary(report),
        )

    def log_report_finalized(
        self, telegram_user: str, report: Report
    ) -> EventLogEntry:
        """Log that a report was finalized.

        Args:
            telegram_user: Who finalized the report.
            report: The finalized report.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_REPORT_FINALIZED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
        )

    def log_report_deleted(
        self, telegram_user: str, report: Report
    ) -> EventLogEntry:
        """Log that a report was deleted.

        Args:
            telegram_user: Who deleted the report.
            report: The deleted report (before deletion).

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_REPORT_DELETED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            old_value=self._report_summary(report),
        )

    def log_report_locked(
        self, telegram_user: str, report: Report
    ) -> EventLogEntry:
        """Log that a report was locked.

        Args:
            telegram_user: Who locked the report.
            report: The locked report.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_REPORT_LOCKED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
        )

    def log_report_unlocked(
        self, telegram_user: str, report: Report
    ) -> EventLogEntry:
        """Log that a report was unlocked.

        Args:
            telegram_user: Who unlocked the report.
            report: The unlocked report.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_REPORT_UNLOCKED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
        )

    # --- Convenience methods for report items ---

    def log_contractor_added(
        self, telegram_user: str, report: Report,
        contractor_name: str,
    ) -> EventLogEntry:
        """Log that a contractor was added to a report.

        Args:
            telegram_user: Who added the contractor.
            report: The affected report.
            contractor_name: Name of the added contractor.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_CONTRACTOR_ADDED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            new_value=contractor_name,
        )

    def log_contractor_removed(
        self, telegram_user: str, report: Report,
        contractor_name: str,
    ) -> EventLogEntry:
        """Log that a contractor was removed from a report.

        Args:
            telegram_user: Who removed the contractor.
            report: The affected report.
            contractor_name: Name of the removed contractor.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_CONTRACTOR_REMOVED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            old_value=contractor_name,
        )

    def log_workers_changed(
        self, telegram_user: str, report: Report,
        contractor_name: str,
        old_workers: int,
        new_workers: int,
    ) -> EventLogEntry:
        """Log that worker count changed for a contractor.

        Args:
            telegram_user: Who changed the workers.
            report: The affected report.
            contractor_name: Name of the contractor.
            old_workers: Previous worker count.
            new_workers: New worker count.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_WORKERS_CHANGED,
            object_type="report_item",
            object_id=report.id,
            object_date=report.date,
            old_value=str(old_workers),
            new_value=str(new_workers),
        )

    # --- Convenience methods for other actions ---

    def log_pdf_generated(
        self, telegram_user: str, report: Report, pdf_path: str
    ) -> EventLogEntry:
        """Log that a PDF was generated for a report.

        Args:
            telegram_user: Who generated the PDF.
            report: The affected report.
            pdf_path: Path to the generated PDF.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_PDF_GENERATED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            new_value=pdf_path,
        )

    def log_version_created(
        self, telegram_user: str, report: Report, version_number: int
    ) -> EventLogEntry:
        """Log that a version snapshot was created.

        Args:
            telegram_user: Who triggered the version.
            report: The affected report.
            version_number: The version number.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_VERSION_CREATED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            new_value=f"v{version_number}",
        )

    def log_version_restored(
        self, telegram_user: str, report: Report, version_number: int
    ) -> EventLogEntry:
        """Log that a version was restored.

        Args:
            telegram_user: Who restored the version.
            report: The affected report (new draft).
            version_number: The restored version number.

        Returns:
            The EventLogEntry.
        """
        return self._repo.log(
            telegram_user=telegram_user,
            action=EVENT_VERSION_RESTORED,
            object_type="report",
            object_id=report.id,
            object_date=report.date,
            old_value=f"v{version_number}",
        )

    # --- Query methods ---

    def get_by_user(
        self, telegram_user: str, limit: int = 50
    ) -> list[EventLogEntry]:
        """Get events for a specific user, most recent first.

        Args:
            telegram_user: The Telegram user identifier.
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        return self._repo.get_by_user(telegram_user, limit=limit)

    def get_by_action(
        self, action: str, limit: int = 50
    ) -> list[EventLogEntry]:
        """Get events of a specific type, most recent first.

        Args:
            action: Action identifier to filter by.
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        return self._repo.get_by_action(action, limit=limit)

    def get_by_object(
        self, object_type: str, object_id: int
    ) -> list[EventLogEntry]:
        """Get events for a specific object.

        Args:
            object_type: Type of object.
            object_id: ID of the object.

        Returns:
            List of EventLogEntry objects.
        """
        return self._repo.get_by_object(object_type, object_id)

    def get_recent(self, limit: int = 20) -> list[EventLogEntry]:
        """Get the most recent events across all users.

        Args:
            limit: Maximum number of results.

        Returns:
            List of EventLogEntry objects.
        """
        return self._repo.get_recent(limit=limit)

    def get_by_date_range(
        self, start: str, end: str
    ) -> list[EventLogEntry]:
        """Get events within a timestamp range.

        Args:
            start: Start timestamp (ISO format).
            end: End timestamp (ISO format).

        Returns:
            List of EventLogEntry objects.
        """
        return self._repo.get_by_date_range(start, end)

    def cleanup_old_events(self, days: int = 90) -> int:
        """Delete events older than the specified number of days.

        Args:
            days: Maximum age in days (default 90).

        Returns:
            Number of deleted events.
        """
        return self._repo.cleanup_old_events(days=days)

    # --- Private helpers ---

    @staticmethod
    def _report_summary(report: Report) -> str:
        """Create a concise JSON summary of a report.

        Args:
            report: The report to summarize.

        Returns:
            JSON string with key report fields.
        """
        import json
        summary = {
            "date": report.date,
            "day": report.day,
            "status": report.status.value if report.status else None,
            "items_count": len(report.items) if report.items else 0,
        }
        return json.dumps(summary, ensure_ascii=False)
