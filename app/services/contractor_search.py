"""
Contractor Search Service for Labor-Report.

Combines the TablesReaderService (for reading contractor data from tables.xlsx)
with the RecentContractorRepository (for per-user recently used contractor cache)
to provide a unified contractor search and selection interface.
"""

from typing import Optional

from app.models.config import AppConfig
from app.models.database import Contractor, Zone
from app.repositories.contractor_repository import ContractorRepository
from app.repositories.recent_contractor_repository import RecentContractorRepository
from app.services.tables_reader import TablesReaderService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContractorSearchService:
    """Unified contractor search with combined database-table and recent-cache lookups.

    Provides fast search across the full contractor list (from tables.xlsx
    AND the database contractors table) and per-user recently used contractors.

    Usage:
        search_service = ContractorSearchService(config, recent_repo, contractor_repo)
        search_service.load_tables()

        # Search all contractors
        results = search_service.search("civil")

        # Get recently used
        recent = search_service.get_recent("user123")

        # Record a selection
        search_service.record_usage("user123", "Civil Contractor")
    """

    def __init__(
        self,
        config: AppConfig,
        recent_repo: RecentContractorRepository,
        contractor_repo: Optional[ContractorRepository] = None,
    ) -> None:
        """Initialize the contractor search service.

        Args:
            config: Application configuration.
            recent_repo: Repository for tracking recently used contractors.
            contractor_repo: Optional repository for user-added contractors.
                            When provided, database contractors are included
                            in search results.
        """
        self._tables_reader = TablesReaderService(config)
        self._recent_repo = recent_repo
        self._contractor_repo = contractor_repo

    # --- Delegation to TablesReaderService ---

    def load_tables(self) -> None:
        """Load contractor data from tables.xlsx.

        Must be called at least once before any search operations.
        Safe to call multiple times (reloads data).
        """
        self._tables_reader.load()

    def is_loaded(self) -> bool:
        """Check if contractor data has been loaded."""
        return self._tables_reader.is_loaded()

    def search(self, query: str, max_results: int = 20) -> list[Contractor]:
        """Search contractors by name (case-insensitive, partial match).

        Searches both the tables.xlsx contractor list AND any user-added
        contractors in the database. Results are merged, deduplicated by
        name, and sorted by relevance.

        Args:
            query: Search text.
            max_results: Maximum results.

        Returns:
            Matching contractors sorted by relevance.
        """
        # Search tables.xlsx
        table_results = self._tables_reader.search_contractors(query, max_results)

        # Search database contractors
        db_results: list[Contractor] = []
        if self._contractor_repo is not None:
            db_results = self._contractor_repo.search(query, max_results)

        # Merge: deduplicate by (name, type) so same-name contractors with
        # different types (e.g. عماله vs صب أعتاب) are kept (tables.xlsx takes priority)
        seen = set()
        merged: list[Contractor] = []
        for c in table_results + db_results:
            key = (c.name.lower().strip(), (c.type or "").lower().strip())
            if key not in seen:
                seen.add(key)
                merged.append(c)

        return merged[:max_results]

    def get_by_name(self, name: str) -> Optional[Contractor]:
        """Get a contractor by exact name.

        Checks tables.xlsx first, then database contractors.

        Args:
            name: Full contractor name (case-insensitive).

        Returns:
            Contractor or None.
        """
        contractor = self._tables_reader.get_contractor_by_name(name)
        if contractor is not None:
            return contractor
        if self._contractor_repo is not None:
            return self._contractor_repo.get_by_name(name)
        return None

    def get_all_contractors(self) -> list[Contractor]:
        """Get all contractors from all sources."""
        table_contractors = self._tables_reader.get_all_contractors()
        if self._contractor_repo is None:
            return table_contractors

        db_contractors = self._contractor_repo.get_all()
        # Merge: deduplicate by (name, type) so same-name contractors with
        # different types are kept (tables.xlsx takes priority)
        seen = set()
        merged: list[Contractor] = []
        for c in table_contractors + db_contractors:
            key = (c.name.lower().strip(), (c.type or "").lower().strip())
            if key not in seen:
                seen.add(key)
                merged.append(c)
        return merged

    def get_all_zones(self) -> list[Zone]:
        """Get all zones (delegates to tables reader)."""
        return self._tables_reader.get_all_zones()

    def get_contractor_count(self) -> int:
        """Get total number of contractors."""
        return self._tables_reader.get_contractor_count()

    # --- Recently used contractor management ---

    def get_recent(self, telegram_user: str, limit: int = 10) -> list[Contractor]:
        """Get recently used contractors for a user.

        Returns full Contractor objects (not just names) by looking up
        the recent names in the loaded contractor data.

        Args:
            telegram_user: Telegram user identifier.
            limit: Maximum number of results.

        Returns:
            List of Contractor objects, ordered by most recently used.
        """
        recent_names = self._recent_repo.get_recent_names(telegram_user, limit)
        contractors: list[Contractor] = []
        for name in recent_names:
            contractor = self._tables_reader.get_contractor_by_name(name)
            if contractor:
                contractors.append(contractor)
            else:
                # Contractor might have been removed from tables.xlsx.
                # Still include it as a minimal entry.
                contractors.append(Contractor(name=name, type="[deleted]"))
        return contractors

    def record_usage(self, telegram_user: str, contractor_name: str) -> None:
        """Record that a user selected a contractor.

        Args:
            telegram_user: Telegram user identifier.
            contractor_name: The selected contractor name.
        """
        self._recent_repo.record_usage(telegram_user, contractor_name)

    def record_usage_batch(self, telegram_user: str, contractor_names: list[str]) -> None:
        """Record usage of multiple contractors at once.

        Args:
            telegram_user: Telegram user identifier.
            contractor_names: List of contractor names used.
        """
        self._recent_repo.record_usage_batch(telegram_user, contractor_names)

    def clear_recent(self, telegram_user: str) -> int:
        """Clear recently used contractors for a user.

        Args:
            telegram_user: Telegram user identifier.

        Returns:
            Number of records deleted.
        """
        return self._recent_repo.clear_for_user(telegram_user)

    # --- Validation ---

    def validate_contractor_exists(self, name: str) -> bool:
        """Check if a contractor exists in tables.xlsx OR database.

        Args:
            name: Contractor name (case-insensitive).

        Returns:
            True if the contractor exists in any source.
        """
        if self._tables_reader.get_contractor_by_name(name) is not None:
            return True
        if self._contractor_repo is not None:
            return self._contractor_repo.exists(name)
        return False

    def get_most_used(self, telegram_user: str, limit: int = 5) -> list[Contractor]:
        """Get most frequently used contractors for a user.

        Args:
            telegram_user: Telegram user identifier.
            limit: Maximum number of results.

        Returns:
            List of Contractor objects, ordered by usage count descending.
        """
        # Note: For now this is just recent. A future optimization could
        # add a separate query ordering by use_count.
        return self.get_recent(telegram_user, limit)
