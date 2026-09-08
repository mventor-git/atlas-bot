"""Contractor Timeline Service for mventor-ticket-016.

Provides business logic for retrieving paginated historical timelines
for individual contractors. Acts as a facade over the repository layer.
"""

from typing import Optional

from app.models.contractor_timeline import TimelineEntry, TimelineResult
from app.repositories.contractor_timeline_repository import (
    ContractorTimelineRepository,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContractorTimelineService:
    """Service for contractor historical timeline queries.

    Provides paginated access to a contractor's complete work history,
    including dates, worker counts, zones, and file references.
    All queries are delegated to the repository layer.
    """

    DEFAULT_PAGE_SIZE = 10

    def __init__(
        self,
        repository: ContractorTimelineRepository,
    ) -> None:
        """Initialize the contractor timeline service.

        Args:
            repository: Repository for contractor timeline queries.
        """
        self._repo = repository

    def get_timeline(
        self,
        contractor: str,
        page: int = 1,
        page_size: Optional[int] = None,
    ) -> TimelineResult:
        """Get a paginated timeline for a specific contractor.

        Args:
            contractor: Contractor name (case-insensitive partial match).
            page: Page number (1-indexed, default 1).
            page_size: Entries per page. Uses DEFAULT_PAGE_SIZE if None.

        Returns:
            TimelineResult with paginated entries and metadata.
            If the contractor has no history, entries will be empty.
        """
        if page_size is None:
            page_size = self.DEFAULT_PAGE_SIZE

        logger.debug(
            "Getting timeline for '%s' (page=%d, page_size=%d)",
            contractor,
            page,
            page_size,
        )

        return self._repo.get_timeline(contractor, page, page_size)

    def get_all_contractor_dates(self, contractor: str) -> list[str]:
        """Get all distinct dates a contractor worked.

        Args:
            contractor: Contractor name.

        Returns:
            Sorted list of date strings (descending), empty if none.
        """
        return self._repo.get_all_contractor_dates(contractor)

    def contractor_exists(self, contractor: str) -> bool:
        """Check if a contractor has any work history.

        Args:
            contractor: Contractor name to check.

        Returns:
            True if the contractor appears in at least one report item.
        """
        return self._repo.contractor_exists(contractor)

    def get_entry_count(self, contractor: str) -> int:
        """Get the total number of timeline entries for a contractor.

        Convenience method that fetches page 1 with page_size=1 and
        reads total_entries from the result metadata.

        Args:
            contractor: Contractor name.

        Returns:
            Total number of timeline entries.
        """
        result = self._repo.get_timeline(contractor, page=1, page_size=1)
        return result.total_entries
