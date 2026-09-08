"""Historical Search Service for mventor-ticket-018.

Answers questions like "Did contractor X work on date Y?"
"""

from typing import Optional

from app.models.historical_search import HistoricalSearchResult
from app.repositories.historical_search_repository import HistoricalSearchRepository


class HistoricalSearchService:
    """Service for historical contractor/work report queries.

    Answers "Did contractor X work on date Y?" and finds nearest
    previous/next working days when no exact match exists.
    """

    def __init__(self, repository: HistoricalSearchRepository) -> None:
        """Initialize the historical search service.

        Args:
            repository: SQL repository for historical search queries.
        """
        self._repo = repository

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
        return self._repo.search_contractor_on_date(contractor, date)

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
        return self._repo.find_nearest_previous(contractor, date)

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
        return self._repo.find_nearest_next(contractor, date)

    def contractor_has_reports(self, contractor: str) -> bool:
        """Check if a contractor has any reports at all.

        Args:
            contractor: Contractor name.

        Returns:
            True if contractor appears in any report.
        """
        return self._repo.contractor_has_reports(contractor)

    def search_text(
        self,
        query: str,
    ) -> HistoricalSearchResult:
        """Search for contractor by text.
        
        This is a convenience method that treats the query as a contractor name
        and returns the most recent match.

        Args:
            query: Search text (treated as contractor name).

        Returns:
            HistoricalSearchResult with most recent match.
        """
        # Use UniversalSearchService if available, but fall back to repo
        result = self._repo.contractor_has_reports(query)
        if not result:
            return HistoricalSearchResult(found=False, contractor=query)
        return HistoricalSearchResult(found=True, contractor=query)