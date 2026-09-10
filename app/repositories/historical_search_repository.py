"""Historical Search Repository for mventor-ticket-018."""

from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.historical_search import HistoricalSearchResult, HistoricalSearchMatch


class HistoricalSearchRepository:
    """SQL-based historical search for report history queries.

    Answers "Did contractor X work on date Y?" and finds
    nearest previous/next working days.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the historical search repository.

        Args:
            db_manager: Database manager for parameterized queries.
        """
        self._db = db_manager

    def search_contractor_on_date(
        self,
        contractor: str,
        date: str,
    ) -> HistoricalSearchResult:
        """Check if a contractor worked on a specific date.

        If found, returns result with report details.
        If not found, also returns nearest_previous and nearest_next matches.

        Args:
            contractor: Contractor name (case-insensitive partial match).
            date: Date in YYYY-MM-DD format.

        Returns:
            HistoricalSearchResult with match info or nearest dates.
        """
        query_lower = contractor.lower().strip()
        if not query_lower:
            return HistoricalSearchResult(found=False)

        # Find exact date match first
        exact_match = self._get_exact_match(query_lower, date)

        if exact_match:
            return HistoricalSearchResult(
                found=True,
                date=exact_match.date,
                day=exact_match.day,
                workers=exact_match.workers,
                zone=exact_match.zone,
                details=exact_match.details,
                report_id=exact_match.report_id,
                pdf_path=exact_match.pdf_path,
                excel_path=exact_match.excel_path,
                contractor=contractor,
                contractor_code=exact_match.contractor_code,
            )

        # No exact match - find nearest previous and next
        previous = self._find_nearest_previous(query_lower, date)
        next_match = self._find_nearest_next(query_lower, date)

        return HistoricalSearchResult(
            found=False,
            nearest_previous=previous,
            nearest_next=next_match,
        )

    def contractor_has_reports(self, contractor: str, site_id: str | None = None) -> bool:
        """Check if a contractor has any reports at all.

        Args:
            contractor: Contractor name to search for.

        Returns:
            True if contractor appears in any report.
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

    def find_nearest_previous(
        self,
        contractor: str,
        date: str,
    ) -> Optional[HistoricalSearchResult]:
        """Find the nearest previous working day for a contractor.

        Args:
            contractor: Contractor name.
            date: Reference date (YYYY-MM-DD).

        Returns:
            Result with nearest previous working day, or None if none exists.
        """
        query_lower = contractor.lower().strip()
        if not query_lower:
            return None

        match = self._find_nearest_previous(query_lower, date)
        if not match:
            return None

        return HistoricalSearchResult(
            found=False,
            date=match.date,
            day=match.day,
            workers=match.workers,
            zone=match.zone,
            details=match.details,
            report_id=match.report_id,
            pdf_path=match.pdf_path,
            excel_path=match.excel_path,
            contractor=contractor,
            contractor_code=match.contractor_code,
        )

    def find_nearest_next(
        self,
        contractor: str,
        date: str,
    ) -> Optional[HistoricalSearchResult]:
        """Find the nearest next working day for a contractor.

        Args:
            contractor: Contractor name.
            date: Reference date (YYYY-MM-DD).

        Returns:
            Result with nearest next working day, or None if none exists.
        """
        query_lower = contractor.lower().strip()
        if not query_lower:
            return None

        match = self._find_nearest_next(query_lower, date)
        if not match:
            return None

        return HistoricalSearchResult(
            found=False,
            date=match.date,
            day=match.day,
            workers=match.workers,
            zone=match.zone,
            details=match.details,
            report_id=match.report_id,
            pdf_path=match.pdf_path,
            excel_path=match.excel_path,
            contractor=contractor,
            contractor_code=match.contractor_code,
        )

    # --- Private helper methods ---

    def _get_exact_match(
        self, q_lower: str, date: str, site_id: str | None = None
    ) -> Optional[HistoricalSearchMatch]:
        """Get report for exact date where contractor matches."""
        row = self._db.execute(
            """SELECT r.id AS report_id, r.date, r.day,
                      ri.workers, ri.zone, ri.details,
                      ri.contractor_code, r.pdf_path, r.excel_path
               FROM reports r
               JOIN report_items ri ON ri.report_id = r.id
               WHERE r.date = ? AND r.site_id = ? AND LOWER(ri.contractor) LIKE ?
               LIMIT 1""",
            (date, site_id or driver.site_id(), f"%{q_lower}%"),
        ).fetchone()

        if row is None:
            return None
        return HistoricalSearchMatch(
            report_id=row["report_id"],
            date=row["date"],
            day=row["day"],
            workers=row["workers"],
            zone=row["zone"],
            details=row["details"],
            contractor_code=row["contractor_code"],
            pdf_path=row["pdf_path"],
            excel_path=row["excel_path"],
        )

    def _find_nearest_previous(
        self, q_lower: str, date: str, site_id: str | None = None
    ) -> Optional[HistoricalSearchMatch]:
        """Find nearest report before date where contractor appears."""
        row = self._db.execute(
            """SELECT r.id AS report_id, r.date, r.day,
                      ri.workers, ri.zone, ri.details,
                      ri.contractor_code, r.pdf_path, r.excel_path
               FROM reports r
               JOIN report_items ri ON ri.report_id = r.id
               WHERE r.date < ? AND r.site_id = ? AND LOWER(ri.contractor) LIKE ?
               ORDER BY r.date DESC
               LIMIT 1""",
            (date, site_id or driver.site_id(), f"%{q_lower}%"),
        ).fetchone()

        if row is None:
            return None
        return HistoricalSearchMatch(
            report_id=row["report_id"],
            date=row["date"],
            day=row["day"],
            workers=row["workers"],
            zone=row["zone"],
            details=row["details"],
            contractor_code=row["contractor_code"],
            pdf_path=row["pdf_path"],
            excel_path=row["excel_path"],
        )

    def _find_nearest_next(
        self, q_lower: str, date: str, site_id: str | None = None
    ) -> Optional[HistoricalSearchMatch]:
        """Find nearest report after date where contractor appears."""
        row = self._db.execute(
            """SELECT r.id AS report_id, r.date, r.day,
                      ri.workers, ri.zone, ri.details,
                      ri.contractor_code, r.pdf_path, r.excel_path
               FROM reports r
               JOIN report_items ri ON ri.report_id = r.id
               WHERE r.date > ? AND r.site_id = ? AND LOWER(ri.contractor) LIKE ?
               ORDER BY r.date ASC
               LIMIT 1""",
            (date, site_id or driver.site_id(), f"%{q_lower}%"),
        ).fetchone()

        if row is None:
            return None
        return HistoricalSearchMatch(
            report_id=row["report_id"],
            date=row["date"],
            day=row["day"],
            workers=row["workers"],
            zone=row["zone"],
            details=row["details"],
            contractor_code=row["contractor_code"],
            pdf_path=row["pdf_path"],
            excel_path=row["excel_path"],
        )