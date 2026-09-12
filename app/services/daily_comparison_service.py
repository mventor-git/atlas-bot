"""Daily Comparison Service for mventor-ticket-019.

Compares two daily labor reports side-by-side and identifies
added/removed/changed contractors and worker counts.
"""

from typing import Optional

from app.models.comparison import ComparisonResult
from app.models.database import Report, ReportItem
from app.repositories.report_repository import ReportRepository


class DailyComparisonError(Exception):
    """Exception raised when a comparison cannot be performed."""

    def __init__(self, message: str) -> None:
        """Initialize with a user-facing message.

        Args:
            message: Human-readable error description.
        """
        self.message = message
        super().__init__(message)


class DailyComparisonService:
    """Service for comparing two daily labor reports.

    Usage:
        service = DailyComparisonService(report_repo)
        result = service.compare("2026-07-10", "2026-07-11")
        if result.has_differences:
            print(result.format_summary())
    """

    def __init__(self, report_repository: ReportRepository) -> None:
        """Initialize the comparison service.

        Args:
            report_repository: Repository for fetching reports.
        """
        self._repo = report_repository

    def compare(self, date_a: str, date_b: str,
                site_id: str | None = None) -> ComparisonResult:
        """Compare two reports and return their differences.

        Both reports must exist in the database. If either is missing,
        a DailyComparisonError is raised.

        Args:
            date_a: First report date (YYYY-MM-DD).
            date_b: Second report date (YYYY-MM-DD).
            site_id: Tenant site (029; defaults to deployment origin).

        Returns:
            ComparisonResult with summary and detailed diffs.

        Raises:
            DailyComparisonError: If either report does not exist.
        """
        report_a = self._repo.get_by_date(date_a, site_id=site_id)
        report_b = self._repo.get_by_date(date_b, site_id=site_id)

        if report_a is None:
            raise DailyComparisonError(
                f"No report exists for {date_a}. "
                "Cannot perform comparison."
            )
        if report_b is None:
            raise DailyComparisonError(
                f"No report exists for {date_b}. "
                "Cannot perform comparison."
            )

        return self._compute_comparison(report_a, report_b)

    def compare_reports(self, report_a: Report, report_b: Report) -> ComparisonResult:
        """Compare two report objects directly.

        Useful when reports are already loaded and no database
        lookup is needed.

        Args:
            report_a: First report.
            report_b: Second report.

        Returns:
            ComparisonResult with summary and detailed diffs.
        """
        return self._compute_comparison(report_a, report_b)

    def _compute_comparison(self, report_a: Report, report_b: Report) -> ComparisonResult:
        """Compute the comparison between two report objects.

        Args:
            report_a: First report.
            report_b: Second report.

        Returns:
            ComparisonResult with all computed diffs.
        """
        items_a = report_a.items or []
        items_b = report_b.items or []

        added, removed, changed = self._diff_contractors(items_a, items_b)

        return ComparisonResult(
            date_a=report_a.date,
            date_b=report_b.date,
            contractor_count_a=len(items_a),
            contractor_count_b=len(items_b),
            worker_count_a=sum((i.workers or 0) for i in items_a),
            worker_count_b=sum((i.workers or 0) for i in items_b),
            added_contractors=added,
            removed_contractors=removed,
            changed_workers=changed,
            status_a=report_a.status.value if report_a.status else None,
            status_b=report_b.status.value if report_b.status else None,
        )

    def _diff_contractors(
        self,
        items_a: list[ReportItem],
        items_b: list[ReportItem],
    ) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[tuple[str, int, int]]]:
        """Calculate added, removed, and changed contractors between two item lists.

        Contractors are matched by name (case-insensitive). A contractor is:
        - 'added' if present in B but not in A
        - 'removed' if present in A but not in B
        - 'changed' if present in both but with different worker counts

        Args:
            items_a: Items from the first report.
            items_b: Items from the second report.

        Returns:
            Tuple of (added, removed, changed) where:
            - added: list[(name, workers_b)]
            - removed: list[(name, workers_a)]
            - changed: list[(name, workers_a, workers_b)]
        """
        # Build lookup dicts keyed by lowercased contractor name
        lookup_a: dict[str, ReportItem] = {}
        for item in items_a:
            key = (item.contractor or "").strip().lower()
            if key:
                lookup_a[key] = item

        lookup_b: dict[str, ReportItem] = {}
        for item in items_b:
            key = (item.contractor or "").strip().lower()
            if key:
                lookup_b[key] = item

        names_a = set(lookup_a.keys())
        names_b = set(lookup_b.keys())

        added: list[tuple[str, int]] = []
        removed: list[tuple[str, int]] = []
        changed: list[tuple[str, int, int]] = []

        # Added: in B but not in A
        for key in sorted(names_b - names_a):
            item = lookup_b[key]
            added.append((item.contractor or key, item.workers or 0))

        # Removed: in A but not in B
        for key in sorted(names_a - names_b):
            item = lookup_a[key]
            removed.append((item.contractor or key, item.workers or 0))

        # Changed: in both, different worker count
        for key in sorted(names_a & names_b):
            item_a = lookup_a[key]
            item_b = lookup_b[key]
            workers_a = item_a.workers or 0
            workers_b = item_b.workers or 0
            if workers_a != workers_b:
                changed.append((item_a.contractor or key, workers_a, workers_b))

        return added, removed, changed
