"""Universal Search Service. (mventor-ticket-012)"""

from app.models.database import ReportStatus
from app.models.search import SearchHit, SearchQuery, SearchResult
from app.repositories.search_repository import SearchRepository


class UniversalSearchService:
    """Coordinates universal search across persisted reports."""

    def __init__(self, search_repository: SearchRepository) -> None:
        """Initialize the service.

        Args:
            search_repository: SQL repository for report search queries.
        """
        self._repo = search_repository

    def search(self, query: SearchQuery) -> SearchResult:
        """Execute a universal search across all report fields."""
        normalized = SearchQuery(
            text=query.text,
            exact_date=query.exact_date,
            start_date=query.start_date,
            end_date=query.end_date,
            min_workers=query.min_workers,
            max_workers=query.max_workers,
            status=self._normalize_status(query.status),
            site_id=query.site_id,
            page=max(0, query.page),
            page_size=max(1, query.page_size),
        )
        return self._repo.search(normalized)

    def search_text(
        self,
        text: str,
        *,
        page: int = 0,
        page_size: int = 10,
    ) -> SearchResult:
        """Search by free text with optional pagination."""
        return self.search(SearchQuery(text=text, page=page, page_size=page_size))

    def search_by_contractor(self, name: str) -> list[SearchHit]:
        """Search reports by contractor name."""
        return self.search(SearchQuery(text=name, page_size=100)).hits

    def search_by_date_range(self, start: str, end: str) -> list[SearchHit]:
        """Search reports within a date range."""
        return self.search(
            SearchQuery(start_date=start, end_date=end, page_size=100)
        ).hits

    def search_by_worker_count(self, min_w: int, max_w: int) -> list[SearchHit]:
        """Search reports by worker count range."""
        return self.search(
            SearchQuery(min_workers=min_w, max_workers=max_w, page_size=100)
        ).hits

    @staticmethod
    def _normalize_status(status: str | ReportStatus | None) -> str | None:
        """Normalize status filters to known database values."""
        if status is None:
            return None
        if isinstance(status, ReportStatus):
            return status.value

        value = str(status).strip().lower()
        if not value:
            return None
        try:
            return ReportStatus(value).value
        except ValueError:
            return value
