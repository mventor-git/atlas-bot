"""Search models for Universal Search. (mventor-ticket-012)"""

from dataclasses import dataclass, field
from math import ceil
from typing import Optional

from app.models.database import ReportStatus


@dataclass
class SearchQuery:
    """Structured search criteria for reports and report items."""

    text: str = ""
    exact_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    min_workers: Optional[int] = None
    max_workers: Optional[int] = None
    status: Optional[str | ReportStatus] = None
    page: int = 0
    page_size: int = 10
    site_id: Optional[str] = None

    @property
    def offset(self) -> int:
        """Return the row offset for the requested page."""
        return max(0, self.page) * max(1, self.page_size)

    @property
    def normalized_text(self) -> str:
        """Return normalized free-text search value."""
        return (self.text or "").strip()

    @property
    def normalized_status(self) -> Optional[str]:
        """Return status as a database value, if provided."""
        if self.status is None:
            return None
        if isinstance(self.status, ReportStatus):
            return self.status.value
        return str(self.status).strip().lower() or None

    @property
    def has_criteria(self) -> bool:
        """Return True when the query contains at least one criterion."""
        return any(
            [
                self.normalized_text,
                self.exact_date,
                self.start_date,
                self.end_date,
                self.min_workers is not None,
                self.max_workers is not None,
                self.normalized_status,
            ]
        )


@dataclass
class SearchHit:
    """A single report-level search hit."""

    report_id: int
    date: str
    day: str
    status: ReportStatus
    contractor_count: int = 0
    total_workers: int = 0
    relevance_score: int = 0
    matched_fields: list[str] = field(default_factory=list)
    matched_contractor: Optional[str] = None
    matched_contractor_code: Optional[str] = None
    matched_type: Optional[str] = None
    matched_zone: Optional[str] = None


@dataclass
class SearchResult:
    """Paginated universal search result."""

    query: SearchQuery
    hits: list[SearchHit]
    total_count: int

    @property
    def page(self) -> int:
        """Return the current zero-based page."""
        return max(0, self.query.page)

    @property
    def page_size(self) -> int:
        """Return the effective page size."""
        return max(1, self.query.page_size)

    @property
    def total_pages(self) -> int:
        """Return the total number of pages."""
        if self.total_count == 0:
            return 1
        return max(1, ceil(self.total_count / self.page_size))

    @property
    def has_results(self) -> bool:
        """Return True when the search returned at least one hit."""
        return self.total_count > 0

    @property
    def no_results_message(self) -> str:
        """Return a user-facing no-results message."""
        text = self.query.normalized_text
        if text:
            return f"No results found for '{text}'."
        return "No results found for the selected filters."
