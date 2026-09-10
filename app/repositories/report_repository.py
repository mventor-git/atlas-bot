"""
Report repository implementation for Labor-Report.

Provides CRUD operations for Report entities with
duplicate detection and date-based queries.
"""

from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.database import driver
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.base import BaseRepository
from app.repositories.event_log_repository import (
    EventLogRepository,
    EVENT_REPORT_CREATED,
    EVENT_REPORT_DELETED,
    EVENT_DRAFT_SAVED,
)
from app.utils.exceptions import DatabaseError, ReportLifecycleError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ReportRepository(BaseRepository[Report]):
    """Repository for Report entity operations.

    Manages persistence of daily labor reports and their items.
    Enforces one-report-per-date constraint (duplicate detection).

    Usage:
        repo = ReportRepository(db_manager)
        report = repo.get_by_date("2026-08-15")
        if report is None:
            repo.add(new_report)
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        event_log_repo: Optional[EventLogRepository] = None,
    ) -> None:
        """Initialize the repository.

        Args:
            db_manager: The database manager instance.
            event_log_repo: Optional EventLogRepository for audit logging.
                           If None, no events are logged (backward-compatible).
        """
        self._db = db_manager
        self._event_log = event_log_repo

    # --- BaseRepository implementation ---

    def get_by_id(self, report_id: int) -> Optional[Report]:
        """Get a report by its ID, including items.

        Args:
            report_id: The report ID.

        Returns:
            Report with items if found, None otherwise.
        """
        row = self._db.execute(
            "SELECT * FROM reports WHERE id = ?", (report_id,)
        ).fetchone()

        if row is None:
            return None

        report = self._row_to_report(row)
        report.items = self._get_items_for_report(report_id)
        return report

    def get_all(self) -> list[Report]:
        """Get all reports, ordered by date descending.

        Returns:
            List of all reports (without items for performance).
        """
        rows = self._db.execute(
            "SELECT * FROM reports ORDER BY date DESC"
        ).fetchall()
        return [self._row_to_report(row) for row in rows]

    def add(self, report: Report) -> Report:
        """Add a new report.

        Checks for duplicate date within the report's site before inserting.
        If the report has items, they are inserted as well.

        Args:
            report: The report to add (without ID).

        Returns:
            The report with its assigned ID and item IDs.

        Raises:
            DatabaseError: If a report for this date already exists in this site.
        """
        # Check for duplicate (one report per day per site)
        existing = self.get_by_date(report.date, site_id=report.site_id or driver.site_id())
        if existing is not None:
            raise DatabaseError(
                f"A report for {report.date} already exists (ID: {existing.id}). "
                "Use replace_report() to replace it."
            )

        now = datetime.now().isoformat()
        # Ensure new reports start with updated_at = created_at
        created = report.created_at or now
        site = report.site_id or driver.site_id()
        report.site_id = site
        cursor = self._db.execute(
            """INSERT INTO reports
               (date, day, site_id, status, pdf_path, excel_path, created_at, telegram_user,
                updated_at, finalized_at, locked_at, locked_by, source_date, preview_pdf_path)
               VALUES (?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.date,
                report.day,
                site,
                report.status.value,
                report.pdf_path,
                report.excel_path,
                created,
                report.telegram_user,
                report.updated_at or created,
                report.finalized_at,
                report.locked_at,
                report.locked_by,
                report.source_date,
                report.preview_pdf_path,
            ),
        )
        self._db.commit()
        report.id = cursor.lastrowid

        # Insert items if any
        if report.items:
            self._insert_items(report.id, report.items)

        logger.info("Report added: date=%s, id=%s, status=%s", report.date, report.id, report.status.value)

        # Log event if EventLogRepository is configured
        if self._event_log is not None and report.telegram_user:
            import json
            summary = json.dumps({
                "date": report.date,
                "day": report.day,
                "status": report.status.value if report.status else None,
                "items_count": len(report.items) if report.items else 0,
            }, ensure_ascii=False)
            self._event_log.log(
                telegram_user=report.telegram_user,
                action=EVENT_REPORT_CREATED,
                object_type="report",
                object_id=report.id,
                object_date=report.date,
                new_value=summary,
            )

        return report

    def update(
        self, report: Report, *, force: bool = False
    ) -> Report:
        """Update an existing report.

        Replaces all items (delete existing, insert new).

        Args:
            report: The report with updated values (must have ID).
            force: If True, skip lifecycle validation. Used by
                   ReportWorkflowService for status transitions.

        Returns:
            The updated report.

        Raises:
            DatabaseError: If report ID is not set.
            ReportLifecycleError: If the report is not in draft state
                                  and force is False.
        """
        if report.id is None:
            raise DatabaseError("Cannot update a report without an ID.")

        # Lifecycle validation: only draft reports can be edited
        if not force:
            current = self.get_by_id(report.id)
            if current is not None and not current.is_editable:
                raise ReportLifecycleError(
                    f"Cannot edit a '{current.status.value}' report. "
                    "Only draft reports can be modified.",
                    current_status=current.status.value,
                    target_status=report.status.value if report.status else None,
                )

        # Capture old state for event logging (before update)
        old_summary = None
        if self._event_log is not None:
            old_report = self.get_by_id(report.id)
            if old_report is not None:
                import json
                old_summary = json.dumps({
                    "date": old_report.date,
                    "day": old_report.day,
                    "status": old_report.status.value if old_report.status else None,
                    "items_count": len(old_report.items) if old_report.items else 0,
                }, ensure_ascii=False)

        # Auto-set updated_at on every modification
        now = datetime.now().isoformat()

        self._db.execute(
            """UPDATE reports
               SET date=?, day=?, status=?, pdf_path=?, excel_path=?,
                   telegram_user=?, updated_at=?,
                   finalized_at=?, locked_at=?, locked_by=?,
                   source_date=?, preview_pdf_path=?
               WHERE id=?""",
            (
                report.date,
                report.day,
                report.status.value,
                report.pdf_path,
                report.excel_path,
                report.telegram_user,
                report.updated_at or now,
                report.finalized_at,
                report.locked_at,
                report.locked_by,
                report.source_date,
                report.preview_pdf_path,
                report.id,
            ),
        )

        # Replace items: delete old, insert new
        self._db.execute("DELETE FROM report_items WHERE report_id=?", (report.id,))
        if report.items:
            self._insert_items(report.id, report.items)

        self._db.commit()

        logger.info("Report updated: id=%s, date=%s", report.id, report.date)

        # Log event if EventLogRepository is configured
        if self._event_log is not None and report.telegram_user:
            import json
            new_summary = json.dumps({
                "date": report.date,
                "day": report.day,
                "status": report.status.value if report.status else None,
                "items_count": len(report.items) if report.items else 0,
            }, ensure_ascii=False)
            action = EVENT_DRAFT_SAVED
            self._event_log.log(
                telegram_user=report.telegram_user,
                action=action,
                object_type="report",
                object_id=report.id,
                object_date=report.date,
                old_value=old_summary,
                new_value=new_summary,
            )

        return report

    def delete(self, report_id: int) -> bool:
        """Delete a report and its items by ID.

        Args:
            report_id: The ID of the report to delete.

        Returns:
            True if deleted, False if not found.
        """
        # Capture report data for event logging (before deletion)
        report_to_delete = None
        if self._event_log is not None:
            report_to_delete = self.get_by_id(report_id)

        # Items are deleted via CASCADE
        cursor = self._db.execute("DELETE FROM reports WHERE id=?", (report_id,))
        self._db.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Report deleted: id=%s", report_id)

        # Log event if EventLogRepository is configured
        if deleted and self._event_log is not None and report_to_delete is not None:
            import json
            summary = json.dumps({
                "date": report_to_delete.date,
                "day": report_to_delete.day,
                "status": report_to_delete.status.value if report_to_delete.status else None,
                "items_count": len(report_to_delete.items) if report_to_delete.items else 0,
            }, ensure_ascii=False)
            self._event_log.log(
                telegram_user=report_to_delete.telegram_user or "system",
                action=EVENT_REPORT_DELETED,
                object_type="report",
                object_id=report_id,
                object_date=report_to_delete.date,
                old_value=summary,
            )

        return deleted

    def count(self) -> int:
        """Count total reports.

        Returns:
            Total number of reports.
        """
        row = self._db.execute("SELECT COUNT(*) as cnt FROM reports").fetchone()
        return row["cnt"] if row else 0

    # --- Additional query methods ---

    def get_by_date(self, date: str, site_id: str | None = None) -> Optional[Report]:
        """Get a report for a specific date within a site.

        Args:
            date: The date string in YYYY-MM-DD format.
            site_id: Tenant site (defaults to this bot's SITE_ID).

        Returns:
            Report with items if found, None otherwise.
        """
        site = site_id or driver.site_id()
        row = self._db.execute(
            "SELECT * FROM reports WHERE date = ? AND site_id = ?", (date, site)
        ).fetchone()

        if row is None:
            return None

        report = self._row_to_report(row)
        report.items = self._get_items_for_report(report.id)
        return report

    def exists_for_date(self, date: str, site_id: str | None = None) -> bool:
        """Check if a report exists for the given date within a site."""
        site = site_id or driver.site_id()
        row = self._db.execute(
            "SELECT 1 FROM reports WHERE date = ? AND site_id = ? LIMIT 1", (date, site)
        ).fetchone()
        return row is not None

    def get_reports_in_range(self, start_date: str, end_date: str) -> list[Report]:
        """Get all reports within a date range (inclusive).

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            List of reports (without items).
        """
        rows = self._db.execute(
            "SELECT * FROM reports WHERE date >= ? AND date <= ? ORDER BY date ASC",
            (start_date, end_date),
        ).fetchall()
        return [self._row_to_report(row) for row in rows]

    def get_no_report_dates(self) -> list[str]:
        """Get all dates where status is 'no_report'.

        Returns:
            List of date strings.
        """
        rows = self._db.execute(
            "SELECT date FROM reports WHERE status = 'no_report' ORDER BY date DESC"
        ).fetchall()
        return [row["date"] for row in rows]

    def replace_report(self, report: Report) -> Report:
        """Replace an existing report for the same date.

        If a report exists for this date, deletes it first,
        then inserts the new one.

        Args:
            report: The new report data.

        Returns:
            The newly inserted report with ID.

        Raises:
            DatabaseError: If replacement fails.
        """
        existing = self.get_by_date(report.date)
        if existing is not None and existing.id is not None:
            self.delete(existing.id)

        return self.add(report)

    # --- Private helpers ---

    def _row_to_report(self, row) -> Report:
        """Convert a database row to a Report object.

        Args:
            row: sqlite3.Row from query.

        Returns:
            Report instance.
        """
        return Report(
            id=row["id"],
            date=row["date"],
            day=row["day"],
            status=ReportStatus(row["status"]),
            pdf_path=row["pdf_path"],
            excel_path=row["excel_path"],
            created_at=row["created_at"],
            telegram_user=row["telegram_user"],
            updated_at=row["updated_at"] if "updated_at" in row.keys() else None,
            finalized_at=row["finalized_at"] if "finalized_at" in row.keys() else None,
            locked_at=row["locked_at"] if "locked_at" in row.keys() else None,
            locked_by=row["locked_by"] if "locked_by" in row.keys() else None,
            source_date=row["source_date"] if "source_date" in row.keys() else None,
            preview_pdf_path=row["preview_pdf_path"] if "preview_pdf_path" in row.keys() else None,
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
        )

    def _get_items_for_report(self, report_id: int) -> list[ReportItem]:
        """Get all items for a report.

        Args:
            report_id: The report ID.

        Returns:
            List of ReportItem instances.
        """
        rows = self._db.execute(
            "SELECT * FROM report_items WHERE report_id = ? ORDER BY id ASC",
            (report_id,),
        ).fetchall()

        return [
            ReportItem(
                id=row["id"],
                report_id=row["report_id"],
                contractor=row["contractor"],
                type=row["type"],
                zone=row["zone"],
                workers=row["workers"],
                details=row["details"],
                contractor_code=row["contractor_code"] if "contractor_code" in row.keys() else None,
            )
            for row in rows
        ]

    def _insert_items(self, report_id: int, items: list[ReportItem]) -> None:
        """Insert report items for a given report.

        Inserts items individually to capture each generated ID
        and set it back on the item object.

        Args:
            report_id: The report ID to associate items with.
            items: List of ReportItem instances (modified in-place with IDs).
        """
        for item in items:
            cursor = self._db.execute(
                """INSERT INTO report_items (report_id, contractor, type, zone, workers, details, contractor_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    report_id,
                    item.contractor,
                    item.type,
                    item.zone,
                    item.workers,
                    item.details,
                    item.contractor_code,
                ),
            )
            item.id = cursor.lastrowid
            item.report_id = report_id
