"""Contractor Timeline Repository for mventor-ticket-016.

Provides paginated historical timeline queries for individual contractors.
All queries are SQL-based with proper indexing for performance.
"""

from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.contractor_timeline import TimelineEntry, TimelineResult


class ContractorTimelineRepository:
    """SQL-based repository for contractor timeline queries.

    Retrieves paginated historical work days for a specific contractor,
    ordered by date descending. Supports existence checks and
    total entry counting.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the contractor timeline repository.

        Args:
            db_manager: Database manager for parameterized SQL queries.
        """
        self._db = db_manager

    def get_timeline(
        self,
        contractor: str,
        page: int = 1,
        page_size: int = 10,
        site_id: str | None = None,
    ) -> TimelineResult:
        """Get a paginated timeline for a specific contractor.

        Args:
            contractor: Contractor name (case-insensitive partial match).
            page: Page number (1-indexed, default 1).
            page_size: Number of entries per page (default 10).

        Returns:
            TimelineResult with paginated entries and metadata.
        """
        query_lower = contractor.lower().strip()
        if not query_lower:
            return TimelineResult(contractor=contractor)

        # Get total count
        site = site_id or driver.site_id()
        count_row = self._db.execute(
            """SELECT COUNT(*) as cnt
               FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) LIKE ?
               AND r.site_id = ?
               AND r.status != 'no_report'""",
            (f"%{query_lower}%", site),
        ).fetchone()
        total_entries = count_row["cnt"] if count_row else 0

        if total_entries == 0:
            return TimelineResult(contractor=contractor)

        # Calculate total pages
        total_pages = max(1, (total_entries + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size

        # Get paginated entries
        rows = self._db.execute(
            """SELECT r.date, r.day, ri.workers, ri.zone, ri.details,
                      ri.contractor_code, r.id AS report_id, r.status AS report_status,
                      r.pdf_path, r.excel_path
               FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) LIKE ?
               AND r.site_id = ?
               AND r.status != 'no_report'
               ORDER BY r.date DESC, ri.id ASC
               LIMIT ? OFFSET ?""",
            (f"%{query_lower}%", site, page_size, offset),
        ).fetchall()

        entries = [
            TimelineEntry(
                date=row["date"],
                day=row["day"],
                workers=row["workers"],
                zone=row["zone"],
                details=row["details"],
                contractor_code=row["contractor_code"],
                report_id=row["report_id"],
                report_status=row["report_status"],
                pdf_path=row["pdf_path"],
                excel_path=row["excel_path"],
            )
            for row in rows
        ]

        return TimelineResult(
            contractor=contractor,
            entries=entries,
            total_entries=total_entries,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    def get_all_contractor_dates(self, contractor: str, site_id: str | None = None) -> list[str]:
        """Get all distinct dates a contractor worked.

        Args:
            contractor: Contractor name (case-insensitive partial match).

        Returns:
            Sorted list of date strings (descending), empty if none.
        """
        query_lower = contractor.lower().strip()
        if not query_lower:
            return []

        rows = self._db.execute(
            """SELECT DISTINCT r.date
               FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) LIKE ?
               AND r.site_id = ?
               AND r.status != 'no_report'
               ORDER BY r.date DESC""",
            (f"%{query_lower}%", site_id or driver.site_id()),
        ).fetchall()

        return [row["date"] for row in rows]

    def contractor_exists(self, contractor: str, site_id: str | None = None) -> bool:
        """Check if a contractor exists in any report.

        Args:
            contractor: Contractor name to check.

        Returns:
            True if the contractor appears in at least one report item.
        """
        query_lower = contractor.lower().strip()
        if not query_lower:
            return False

        row = self._db.execute(
            """SELECT 1 FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) LIKE ? AND r.site_id = ?
               LIMIT 1""",
            (f"%{query_lower}%", site_id or driver.site_id()),
        ).fetchone()
        return row is not None
