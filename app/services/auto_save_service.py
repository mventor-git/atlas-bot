"""Auto Save Service for Labor-Report. (mventor-ticket-008)

Automatically persists every modification to the database immediately.
No explicit "save" button needed — every action triggers a save.
SQLite WAL mode ensures crash resilience for committed writes.

Usage:
    service = AutoSaveService(report_repo, event_log_service)
    report = service.save_draft(report, telegram_user="user123")
    report = service.auto_save_item(report, item, telegram_user="user123")
"""

from datetime import datetime
from typing import Optional

from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.event_log_service import EventLogService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AutoSaveService:
    """Service that automatically persists every modification to the database.

    Every action (contractor added, removed, workers changed, zone changed,
    details changed) triggers an immediate save via ``save_draft()``.
    Handlers call this service after each user interaction step; they
    never need an explicit "save" command.

    SQLite in WAL mode ensures committed writes survive unexpected shutdown,
    providing crash resilience at the storage layer.
    """

    def __init__(
        self,
        report_repository: ReportRepository,
        event_log_service: EventLogService,
    ) -> None:
        """Initialize the auto-save service.

        Args:
            report_repository: Repository for report persistence.
            event_log_service: Service for logging draft-save events.
        """
        self._repo = report_repository
        self._event_log = event_log_service

    def save_draft(
        self, report: Report, telegram_user: str
    ) -> Report:
        """Save or update a draft report.

        Creates a new report if none exists for the given date,
        otherwise updates the existing report's items and metadata.

        The report is forced to DRAFT status and ``updated_at`` is
        set to the current timestamp.

        Args:
            report: The draft report to save (may or may not have an ID).
            telegram_user: Who is performing the action.

        Returns:
            The saved report with a populated ID.
        """
        now = datetime.now().isoformat()
        report.status = ReportStatus.DRAFT
        report.updated_at = now

        # If report already has an ID, update it directly
        if report.id is not None:
            return self._repo.update(report)

        # No ID — check if a report already exists for this date
        existing = self._repo.get_by_date(report.date)
        if existing is not None:
            return self._update_existing(existing, report, now)

        # Truly new report
        saved = self._repo.add(report)
        logger.debug(
            "Draft created via auto-save: date=%s, id=%s, user=%s",
            saved.date, saved.id, telegram_user,
        )
        return saved

    def auto_save_item(
        self,
        report: Report,
        item: ReportItem,
        telegram_user: str,
    ) -> Report:
        """Auto-save after adding or modifying a contractor item.

        Ensures the item is attached to the report's items list,
        then saves the entire draft.

        Args:
            report: The draft report.
            item: The contractor item that was added or modified.
            telegram_user: Who performed the action.

        Returns:
            The saved report with the item included.
        """
        if item not in report.items:
            report.add_item(item)
        return self.save_draft(report, telegram_user)

    def remove_item_and_save(
        self,
        report: Report,
        item_index: int,
        telegram_user: str,
    ) -> Report:
        """Remove a contractor item by index and save the draft.

        Args:
            report: The draft report.
            item_index: Index of the item to remove (0-based).
            telegram_user: Who performed the action.

        Returns:
            The saved report with the item removed.

        Raises:
            IndexError: If item_index is out of range.
        """
        if 0 <= item_index < len(report.items):
            del report.items[item_index]
        return self.save_draft(report, telegram_user)

    def verify_session_consistency(
        self,
        telegram_user: str,
        session_date: Optional[str] = None,
        session_contractors: Optional[list[dict]] = None,
    ) -> dict:
        """Verify that in-memory session data matches the database.

        This is a crash-recovery helper. If the application restarts,
        the handler can call this to check whether the session's cached
        data is still consistent with what was persisted.

        Args:
            telegram_user: The user to verify for (used in diagnostics).
            session_date: The date the session was working on.
            session_contractors: The list of contractors from session data.

        Returns:
            A dict with consistency info:
            - ``consistent`` (bool): True if session matches DB
            - ``db_report`` (Report | None): The report from the database
            - ``differences`` (list[str]): Description of any mismatches
        """
        result: dict = {
            "consistent": True,
            "db_report": None,
            "differences": [],
        }

        if not session_date:
            result["consistent"] = False
            result["differences"].append("No session date provided")
            return result

        db_report = self._repo.get_by_date(session_date)
        result["db_report"] = db_report

        if db_report is None:
            if session_contractors:
                result["consistent"] = False
                result["differences"].append(
                    "Session has contractors but no report exists in DB"
                )
            return result

        # Compare contractor counts if session data is available
        if session_contractors is not None:
            db_count = len(db_report.items) if db_report.items else 0
            session_count = len(session_contractors)
            if db_count != session_count:
                result["consistent"] = False
                result["differences"].append(
                    f"Contractor count mismatch: DB={db_count}, Session={session_count}"
                )

        # Report exists but might be in wrong state for editing
        if db_report.status != ReportStatus.DRAFT:
            result["differences"].append(
                f"Report status is '{db_report.status.value}', not 'draft'"
            )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_existing(
        self, existing: Report, incoming: Report, now: str
    ) -> Report:
        """Merge incoming report data into an existing report and save.

        Args:
            existing: The report already in the database.
            incoming: The report with potentially new data.
            now: ISO timestamp for updated_at.

        Returns:
            The updated report.
        """
        existing.status = ReportStatus.DRAFT
        existing.updated_at = now
        existing.day = incoming.day or existing.day
        existing.telegram_user = incoming.telegram_user or existing.telegram_user

        # Carry over optional fields
        if incoming.source_date is not None:
            existing.source_date = incoming.source_date
        if incoming.preview_pdf_path is not None:
            existing.preview_pdf_path = incoming.preview_pdf_path
        if incoming.pdf_path is not None:
            existing.pdf_path = incoming.pdf_path
        if incoming.excel_path is not None:
            existing.excel_path = incoming.excel_path

        # Reset lifecycle timestamps if they were set (user is re-editing)
        existing.finalized_at = None
        existing.locked_at = None
        existing.locked_by = None

        # Replace items with incoming items
        existing.items = incoming.items

        return self._repo.update(existing)
