"""
Statistics Engine â€” Daily, monthly, and yearly aggregate statistics.

Provides pre-computed and cached statistics for the labor report system.
Includes averages, top contractors, top zones, and worker trends.
All computation is SQL-based with zero AI dependencies.

mventor-ticket-015: Statistics Engine
"""

from dataclasses import dataclass, field
from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.repositories.stats_cache_repository import StatsCacheRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PeriodStats:
    """Aggregated statistics for a specific time period.

    Represents daily, monthly, or yearly aggregates computed
    from the reports and report_items tables.
    """

    period: str
    """Period type: 'daily', 'monthly', or 'yearly'."""

    period_key: str
    """Period identifier: '2026-07-11', '2026-07', or '2026'."""

    total_reports: int = 0
    """Number of reports in this period (excluding no_report status)."""

    total_contractors: int = 0
    """Total contractor appearances (sum of items across all reports)."""

    total_workers: int = 0
    """Total workers across all reports in this period."""

    avg_contractors: float = 0.0
    """Average contractors per report."""

    avg_workers: float = 0.0
    """Average workers per report."""

    top_contractor: tuple[str, int] = ("", 0)
    """Most active contractor as (name, appearances). Empty if none."""

    top_zone: tuple[str, int] = ("", 0)
    """Most active zone as (name, appearances). Empty if none."""

    trend: Optional[str] = None
    """Trend direction: 'up', 'down', 'stable', or None if not calculable."""

    def to_dict(self) -> dict:
        """Serialize to a dictionary for cache storage.

        Returns:
            Dict representation suitable for JSON serialization.
        """
        return {
            "period": self.period,
            "period_key": self.period_key,
            "total_reports": self.total_reports,
            "total_contractors": self.total_contractors,
            "total_workers": self.total_workers,
            "avg_contractors": self.avg_contractors,
            "avg_workers": self.avg_workers,
            "top_contractor": list(self.top_contractor),
            "top_zone": list(self.top_zone),
            "trend": self.trend,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PeriodStats":
        """Deserialize from a dictionary (cache retrieval).

        Args:
            data: Dict previously created by to_dict().

        Returns:
            PeriodStats instance.
        """
        tc = data.get("top_contractor", ["", 0])
        tz = data.get("top_zone", ["", 0])
        return cls(
            period=data.get("period", ""),
            period_key=data.get("period_key", ""),
            total_reports=data.get("total_reports", 0),
            total_contractors=data.get("total_contractors", 0),
            total_workers=data.get("total_workers", 0),
            avg_contractors=data.get("avg_contractors", 0.0),
            avg_workers=data.get("avg_workers", 0.0),
            top_contractor=tuple(tc) if isinstance(tc, list) else tc,
            top_zone=tuple(tz) if isinstance(tz, list) else tz,
            trend=data.get("trend"),
        )


class StatisticsEngine:
    """Computes and caches aggregate statistics for labor reports.

    Provides daily, monthly, and yearly statistics including:
    - Total reports, contractors, workers
    - Average contractors and workers per report
    - Top contractor and zone by appearances
    - Worker trend direction (up/down/stable)

    Uses StatsCacheRepository for TTL-based caching to avoid
    expensive SQL aggregation on every request.

    Usage:
        engine = StatisticsEngine(db_manager, stats_cache_repo)
        stats = engine.get_daily_stats("2026-07-11")
        monthly = engine.get_monthly_stats(2026, 7)
        yearly = engine.get_yearly_stats(2026)
    """

    VALID_PERIODS = ("daily", "monthly", "yearly")

    def __init__(
        self,
        db_manager: DatabaseManager,
        stats_cache_repo: Optional[StatsCacheRepository] = None,
    ) -> None:
        """Initialize the statistics engine.

        Args:
            db_manager: The database manager for SQL queries.
            stats_cache_repo: Optional cache repository for TTL-based caching.
                             If None, stats are computed fresh every time.
        """
        self._db = db_manager
        self._cache = stats_cache_repo

    def get_period_stats(self, period: str, period_key: str, site_id: str | None = None) -> PeriodStats:
        """Get statistics for a specific period, with caching.

        Checks cache first. On miss, computes from SQL, caches, and returns.

        Args:
            period: Period type ('daily', 'monthly', 'yearly').
            period_key: Period identifier (e.g., '2026-07-11', '2026-07', '2026').

        Returns:
            PeriodStats with all aggregate data.

        Raises:
            ValueError: If period is not one of the valid types.
        """
        if period not in self.VALID_PERIODS:
            raise ValueError(
                f"Invalid period '{period}'. Must be one of {self.VALID_PERIODS}"
            )
        site = site_id or driver.site_id()

        # Try cache first
        cached = self._get_cached(period, period_key, site)
        if cached is not None:
            logger.debug("Cache hit for %s/%s", period, period_key)
            return cached

        # Compute fresh
        stats = self._compute_stats(period, period_key, site)

        # Calculate trend
        stats.trend = self._calculate_trend(period, period_key, site)

        # Cache the result
        self._set_cached(period, period_key, site, stats)

        return stats

    def get_daily_stats(self, date: str, site_id: str | None = None) -> PeriodStats:
        """Get statistics for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format.

        Returns:
            PeriodStats for the given date.
        """
        return self.get_period_stats("daily", date, site_id)

    def get_monthly_stats(self, year: int, month: int, site_id: str | None = None) -> PeriodStats:
        """Get statistics for a specific month.

        Args:
            year: The year (e.g., 2026).
            month: The month (1-12).

        Returns:
            PeriodStats for the given month.

        Raises:
            ValueError: If month is not 1-12.
        """
        if not 1 <= month <= 12:
            raise ValueError(f"Month must be 1-12, got {month}")
        period_key = f"{year:04d}-{month:02d}"
        return self.get_period_stats("monthly", period_key, site_id)

    def get_yearly_stats(self, year: int, site_id: str | None = None) -> PeriodStats:
        """Get statistics for a specific year.

        Args:
            year: The year (e.g., 2026).

        Returns:
            PeriodStats for the given year.
        """
        period_key = f"{year:04d}"
        return self.get_period_stats("yearly", period_key, site_id)

    def calculate_trend(self, period: str, period_key: str, site_id: str | None = None) -> Optional[str]:
        """Calculate the worker trend direction for a period.

        Compares the current period's total workers with the previous
        period's total workers to determine direction.

        Args:
            period: Period type ('daily', 'monthly', 'yearly').
            period_key: Period identifier.

        Returns:
            'up' if workers increased, 'down' if decreased,
            'stable' if unchanged, None if comparison not possible.
        """
        return self._calculate_trend(period, period_key, site_id or driver.site_id())

    def invalidate_cache(self, period: str, period_key: str, site_id: str | None = None) -> bool:
        """Remove a specific cache entry.

        Args:
            period: Period type.
            period_key: Period identifier.
            site_id: Tenant site (defaults to this bot's SITE_ID).

        Returns:
            True if removed, False if not found or no cache.
        """
        if self._cache is None:
            return False
        return self._cache.invalidate(period, self._cache_key(period_key, site_id))

    def invalidate_all_cache(self) -> int:
        """Remove all cached statistics.

        Returns:
            Number of entries removed, or 0 if no cache.
        """
        if self._cache is None:
            return 0
        return self._cache.invalidate_all()

    # --- Private: Cache helpers ---

    @staticmethod
    def _cache_key(period_key: str, site_id: str | None) -> str:
        """Namespace cache keys per site (shared cache table)."""
        return f"{site_id or driver.site_id()}\x00{period_key}"

    def _get_cached(self, period: str, period_key: str, site: str) -> Optional[PeriodStats]:
        """Try to retrieve stats from cache.

        Args:
            period: Period type.
            period_key: Period identifier.

        Returns:
            PeriodStats if cache hit, None otherwise.
        """
        if self._cache is None:
            return None

        data = self._cache.get_stats(period, self._cache_key(period_key, site))
        if data is None:
            return None

        try:
            return PeriodStats.from_dict(data)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Invalid cached data for %s/%s: %s", period, period_key, e)
            return None

    def _set_cached(self, period: str, period_key: str, site: str, stats: PeriodStats) -> None:
        """Store stats in cache.

        Args:
            period: Period type.
            period_key: Period identifier.
            stats: The PeriodStats to cache.
        """
        if self._cache is None:
            return

        try:
            self._cache.set_stats(period, self._cache_key(period_key, site), stats.to_dict())
        except Exception as e:
            logger.warning("Failed to cache stats for %s/%s: %s", period, period_key, e)

    # --- Private: Computation ---

    def _compute_stats(self, period: str, period_key: str, site: str) -> PeriodStats:
        """Compute statistics from SQL queries.

        Args:
            period: Period type.
            period_key: Period identifier.

        Returns:
            PeriodStats with computed values (trend not set).
        """
        date_filter = self._build_date_filter(period, period_key, site)

        # Get report count and basic aggregates
        totals = self._compute_totals(date_filter)

        if totals["total_reports"] == 0:
            return PeriodStats(period=period, period_key=period_key)

        # Get top contractor
        top_contractor = self._get_top_contractor(date_filter)

        # Get top zone
        top_zone = self._get_top_zone(date_filter)

        return PeriodStats(
            period=period,
            period_key=period_key,
            total_reports=totals["total_reports"],
            total_contractors=totals["total_contractors"],
            total_workers=totals["total_workers"],
            avg_contractors=totals["avg_contractors"],
            avg_workers=totals["avg_workers"],
            top_contractor=top_contractor,
            top_zone=top_zone,
        )

    def _build_date_filter(self, period: str, period_key: str, site: str) -> tuple[str, tuple]:
        """Build a SQL WHERE clause fragment for date filtering.

        Args:
            period: Period type.
            period_key: Period identifier.

        Returns:
            Tuple of (sql_fragment, params).
        """
        if period == "daily":
            return "r.site_id = ? AND r.date = ?", (site, period_key)
        elif period == "monthly":
            # period_key is 'YYYY-MM'
            return "r.site_id = ? AND r.date LIKE ?", (site, period_key + "%")
        elif period == "yearly":
            # period_key is 'YYYY'
            return "r.site_id = ? AND r.date LIKE ?", (site, period_key + "%")
        else:
            return "1=0", ()

    def _compute_totals(self, date_filter: tuple[str, tuple]) -> dict:
        """Compute total reports, contractors, workers, and averages.

        Args:
            date_filter: Tuple of (sql_fragment, params) from _build_date_filter.

        Returns:
            Dict with total_reports, total_contractors, total_workers,
            avg_contractors, avg_workers.
        """
        sql_fragment, params = date_filter

        # Count reports (excluding no_report status)
        row = self._db.execute(
            f"""SELECT COUNT(*) as cnt
                FROM reports r
                WHERE {sql_fragment}
                AND r.status != 'no_report'""",
            params,
        ).fetchone()
        total_reports = row["cnt"] if row else 0

        if total_reports == 0:
            return {
                "total_reports": 0,
                "total_contractors": 0,
                "total_workers": 0,
                "avg_contractors": 0.0,
                "avg_workers": 0.0,
            }

        # Aggregate contractors and workers from report_items
        row = self._db.execute(
            f"""SELECT
                    COUNT(ri.id) as total_contractors,
                    COALESCE(SUM(ri.workers), 0) as total_workers
                FROM report_items ri
                JOIN reports r ON ri.report_id = r.id
                WHERE {sql_fragment}
                AND r.status != 'no_report'""",
            params,
        ).fetchone()

        total_contractors = row["total_contractors"] if row else 0
        total_workers = row["total_workers"] if row else 0

        # Averages
        avg_contractors = round(total_contractors / total_reports, 2) if total_reports > 0 else 0.0
        avg_workers = round(total_workers / total_reports, 2) if total_reports > 0 else 0.0

        return {
            "total_reports": total_reports,
            "total_contractors": total_contractors,
            "total_workers": total_workers,
            "avg_contractors": avg_contractors,
            "avg_workers": avg_workers,
        }

    def _get_top_contractor(self, date_filter: tuple[str, tuple]) -> tuple[str, int]:
        """Find the most active contractor in the period.

        Args:
            date_filter: Tuple of (sql_fragment, params).

        Returns:
            Tuple of (contractor_name, appearance_count).
            Returns ("", 0) if no data.
        """
        sql_fragment, params = date_filter

        row = self._db.execute(
            f"""SELECT ri.contractor, COUNT(*) as appearances
                FROM report_items ri
                JOIN reports r ON ri.report_id = r.id
                WHERE {sql_fragment}
                AND r.status != 'no_report'
                GROUP BY ri.contractor
                ORDER BY appearances DESC, ri.contractor ASC
                LIMIT 1""",
            params,
        ).fetchone()

        if row is None:
            return ("", 0)
        return (row["contractor"], row["appearances"])

    def _get_top_zone(self, date_filter: tuple[str, tuple]) -> tuple[str, int]:
        """Find the most active zone in the period.

        Args:
            date_filter: Tuple of (sql_fragment, params).

        Returns:
            Tuple of (zone_name, appearance_count).
            Returns ("", 0) if no data.
        """
        sql_fragment, params = date_filter

        row = self._db.execute(
            f"""SELECT ri.zone, COUNT(*) as appearances
                FROM report_items ri
                JOIN reports r ON ri.report_id = r.id
                WHERE {sql_fragment}
                AND r.status != 'no_report'
                AND ri.zone IS NOT NULL AND ri.zone != ''
                GROUP BY ri.zone
                ORDER BY appearances DESC, ri.zone ASC
                LIMIT 1""",
            params,
        ).fetchone()

        if row is None:
            return ("", 0)
        return (row["zone"], row["appearances"])

    def _calculate_trend(self, period: str, period_key: str, site: str) -> Optional[str]:
        """Calculate the worker trend by comparing to previous period.

        Args:
            period: Period type.
            period_key: Period identifier.

        Returns:
            'up', 'down', 'stable', or None if comparison not possible.
        """
        prev_key = self._get_previous_period_key(period, period_key)
        if prev_key is None:
            return None

        current_workers = self._get_total_workers(period, period_key, site)
        previous_workers = self._get_total_workers(period, prev_key, site)

        # If no data for either period, no trend
        if current_workers == 0 and previous_workers == 0:
            return None

        # If previous had no data, can't compare
        if previous_workers == 0 and current_workers > 0:
            return "up"

        if current_workers > previous_workers:
            return "up"
        elif current_workers < previous_workers:
            return "down"
        else:
            return "stable"

    def _get_total_workers(self, period: str, period_key: str, site: str) -> int:
        """Get total workers for a period (no caching, direct SQL).

        Args:
            period: Period type.
            period_key: Period identifier.

        Returns:
            Total workers count.
        """
        date_filter = self._build_date_filter(period, period_key, site)
        sql_fragment, params = date_filter

        row = self._db.execute(
            f"""SELECT COALESCE(SUM(ri.workers), 0) as total
                FROM report_items ri
                JOIN reports r ON ri.report_id = r.id
                WHERE {sql_fragment}
                AND r.status != 'no_report'""",
            params,
        ).fetchone()

        return row["total"] if row else 0

    def _get_previous_period_key(self, period: str, period_key: str) -> Optional[str]:
        """Calculate the previous period's key.

        Args:
            period: Period type.
            period_key: Current period identifier.

        Returns:
            Previous period key, or None if not calculable.
        """
        if period == "daily":
            # Parse date and subtract one day
            try:
                from datetime import datetime, timedelta
                current = datetime.strptime(period_key, "%Y-%m-%d")
                prev = current - timedelta(days=1)
                return prev.strftime("%Y-%m-%d")
            except ValueError:
                return None

        elif period == "monthly":
            # Parse 'YYYY-MM' and subtract one month
            try:
                parts = period_key.split("-")
                year = int(parts[0])
                month = int(parts[1])
                if month == 1:
                    return f"{year - 1:04d}-12"
                else:
                    return f"{year:04d}-{month - 1:02d}"
            except (ValueError, IndexError):
                return None

        elif period == "yearly":
            try:
                year = int(period_key)
                return f"{year - 1:04d}"
            except ValueError:
                return None

        return None
