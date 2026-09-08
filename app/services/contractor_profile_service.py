"""Contractor Profile Service for mventor-ticket-017.

Builds aggregated contractor profiles from report history data,
computing statistics like first/last appearance, total workers,
favorite zones, and monthly activity.
"""

from typing import Optional

from app.database.manager import DatabaseManager
from app.models.contractor_profile import ContractorProfile
from app.services.tables_reader import TablesReaderService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContractorProfileService:
    """Service for building contractor profiles.

    Aggregates data from report_items and reports tables to produce
    a comprehensive profile for any contractor that has appeared
    in at least one report.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        tables_reader: Optional[TablesReaderService] = None,
    ) -> None:
        """Initialize the contractor profile service.

        Args:
            db_manager: Database manager for SQL queries.
            tables_reader: Optional tables reader for type/code lookup.
        """
        self._db = db_manager
        self._tables_reader = tables_reader

    def get_profile(self, contractor_name: str) -> Optional[ContractorProfile]:
        """Build a full profile for a contractor.

        Args:
            contractor_name: Contractor name (case-insensitive exact match).

        Returns:
            ContractorProfile with aggregated data, or None if the
            contractor has no appearances in any report.
        """
        name = contractor_name.strip()
        if not name:
            return None

        # Check if contractor exists and get basic stats
        stats = self._query_basic_stats(name)
        if stats is None:
            return None

        first_date, last_date, total_reports, total_workers = stats

        # Compute derived fields
        avg_workers = round(total_workers / total_reports, 1) if total_reports > 0 else 0.0
        favorite_zones = self._query_favorite_zones(name)
        monthly_activity = self._query_monthly_activity(name)

        # Look up contractor info from tables.xlsx
        ctype: Optional[str] = None
        code: Optional[str] = None
        if self._tables_reader is not None:
            contractor_info = self._tables_reader.get_contractor_by_name(name)
            if contractor_info is not None:
                ctype = contractor_info.type
                # Code may be looked up from the most recent report item
                code = self._query_latest_code(name)

        return ContractorProfile(
            name=name,
            type=ctype,
            code=code,
            first_appearance=first_date,
            last_appearance=last_date,
            total_reports=total_reports,
            total_workers=total_workers,
            avg_workers=avg_workers,
            favorite_zones=favorite_zones,
            monthly_activity=monthly_activity,
        )

    # --- Private query helpers ---

    def _query_basic_stats(self, name: str) -> Optional[tuple[str, str, int, int]]:
        """Query basic contractor statistics.

        Args:
            name: Contractor name for exact match.

        Returns:
            Tuple of (first_date, last_date, total_reports, total_workers)
            or None if contractor not found.
        """
        row = self._db.execute(
            """SELECT
                   MIN(r.date) AS first_date,
                   MAX(r.date) AS last_date,
                   COUNT(DISTINCT r.id) AS total_reports,
                   COALESCE(SUM(ri.workers), 0) AS total_workers
               FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) = LOWER(?)
               AND r.status != 'no_report'""",
            (name,),
        ).fetchone()

        if row is None or row["first_date"] is None:
            return None

        return (
            row["first_date"],
            row["last_date"],
            row["total_reports"],
            row["total_workers"],
        )

    def _query_favorite_zones(self, name: str, limit: int = 3) -> list[tuple[str, int]]:
        """Get top work zones by frequency for a contractor.

        Args:
            name: Contractor name for exact match.
            limit: Maximum number of zones to return.

        Returns:
            List of (zone_name, count) tuples sorted by count descending.
        """
        rows = self._db.execute(
            """SELECT ri.zone, COUNT(*) AS cnt
               FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) = LOWER(?)
               AND ri.zone IS NOT NULL AND ri.zone != ''
               AND r.status != 'no_report'
               GROUP BY ri.zone
               ORDER BY cnt DESC, ri.zone ASC
               LIMIT ?""",
            (name, limit),
        ).fetchall()

        return [(row["zone"], row["cnt"]) for row in rows]

    def _query_monthly_activity(self, name: str) -> list[tuple[str, int]]:
        """Get monthly worker totals for a contractor.

        Args:
            name: Contractor name for exact match.

        Returns:
            List of (YYYY-MM, total_workers) tuples sorted by month ascending.
        """
        rows = self._db.execute(
            """SELECT STRFTIME('%Y-%m', r.date) AS month,
                      COALESCE(SUM(ri.workers), 0) AS total_workers
               FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) = LOWER(?)
               AND r.status != 'no_report'
               GROUP BY month
               ORDER BY month ASC""",
            (name,),
        ).fetchall()

        return [(row["month"], row["total_workers"]) for row in rows]

    def _query_latest_code(self, name: str) -> Optional[str]:
        """Get the most recent contractor_code for this contractor.

        Args:
            name: Contractor name for exact match.

        Returns:
            Most recent contractor_code, or None.
        """
        row = self._db.execute(
            """SELECT ri.contractor_code
               FROM report_items ri
               JOIN reports r ON ri.report_id = r.id
               WHERE LOWER(ri.contractor) = LOWER(?)
               AND ri.contractor_code IS NOT NULL
               AND r.status != 'no_report'
               ORDER BY r.date DESC, ri.id DESC
               LIMIT 1""",
            (name,),
        ).fetchone()

        return row["contractor_code"] if row else None
