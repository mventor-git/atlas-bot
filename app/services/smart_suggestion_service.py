"""Smart Suggestion Service for Labor-Report. (mventor-ticket-009)

Provides relevance-ordered contractor suggestions when the user is adding
contractors to a report. Ordering: Favorites > Recently Used > Frequently
Used > Yesterday's Contractors > All Others.

No AI model required â€” pure SQL + business logic scoring.
"""

from datetime import datetime, timedelta
from typing import Optional

from app.models.config import AppConfig
from app.models.database import Contractor
from app.repositories.favorites_repository import FavoritesRepository
from app.repositories.recent_contractor_repository import RecentContractorRepository
from app.repositories.report_repository import ReportRepository
from app.services.contractor_search import ContractorSearchService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SmartSuggestionService:
    """Provides relevance-ordered contractor suggestions.

    Combines multiple data sources with a priority ranking:
    1. Favorites (per-user)
    2. Recently used (per-user, by last_used)
    3. Frequently used (global, by total use_count)
    4. Yesterday's contractors
    5. All other contractors (filler)

    Duplicates are removed. The final list is truncated to ``max_results``.
    """

    def __init__(
        self,
        search_service: ContractorSearchService,
        recent_repo: RecentContractorRepository,
        favorites_repo: FavoritesRepository,
        report_repo: ReportRepository,
        config: Optional[AppConfig] = None,
    ) -> None:
        """Initialize the suggestion service.

        Args:
            search_service: Contractor search service (provides access to
                            full contractor list and name resolution).
            recent_repo: Repository for per-user recently used contractors.
            favorites_repo: Repository for per-user favorites.
            report_repo: Repository for report queries (yesterday's contractors).
            config: Application configuration (for suggestion limits).
        """
        self._search = search_service
        self._recent_repo = recent_repo
        self._favorites_repo = favorites_repo
        self._report_repo = report_repo
        self._config = config

    def get_suggestions(
        self,
        telegram_user: str,
        date: str,
        max_results: Optional[int] = None,
    ) -> list[Contractor]:
        """Get smart contractor suggestions ordered by relevance.

        Args:
            telegram_user: The user to get suggestions for.
            date: Today's date (YYYY-MM-DD); used to compute yesterday.
            max_results: Maximum number of suggestions (defaults to config
                         value or 10).

        Returns:
            A list of Contractor objects ordered by relevance, deduplicated,
            and truncated to ``max_results``.
        """
        if max_results is None:
            max_results = self._get_config_max()

        suggested_names: list[str] = []
        seen: set[str] = set()

        # 1. Favorites (highest priority)
        favorites = self._get_favorites(telegram_user)
        for name in favorites:
            if name not in seen:
                suggested_names.append(name)
                seen.add(name)

        # 2. Recently used (by last_used)
        recent = self._get_recent(telegram_user)
        for name in recent:
            if name not in seen:
                suggested_names.append(name)
                seen.add(name)

        # 3. Frequently used (global, by use_count)
        frequent = self._get_frequent_global()
        for name in frequent:
            if name not in seen:
                suggested_names.append(name)
                seen.add(name)

        # 4. Yesterday's contractors
        yesterday_names = self._get_yesterday_contractors(date)
        for name in yesterday_names:
            if name not in seen:
                suggested_names.append(name)
                seen.add(name)

        # 5. All other contractors (filler)
        if len(suggested_names) < max_results:
            all_contractors = self._get_all_contractors()
            for c in all_contractors:
                if c.name not in seen:
                    suggested_names.append(c.name)
                    seen.add(c.name)
                    if len(suggested_names) >= max_results:
                        break

        # Resolve names to Contractor objects and truncate
        result: list[Contractor] = []
        for name in suggested_names[:max_results]:
            contractor = self._search.get_by_name(name)
            if contractor is not None:
                result.append(contractor)
            else:
                # Fallback: name no longer in tables (deleted contractor)
                result.append(Contractor(name=name, type="[deleted]"))

        return result

    # ------------------------------------------------------------------
    # Data source helpers
    # ------------------------------------------------------------------

    def _get_favorites(self, telegram_user: str) -> list[str]:
        """Get favorite contractor names for the user."""
        return self._favorites_repo.get_favorite_names(telegram_user)

    def _get_recent(self, telegram_user: str) -> list[str]:
        """Get recently used contractor names for the user."""
        return self._recent_repo.get_recent_names(
            telegram_user,
            limit=self._get_config_max(),
        )

    def _get_frequent_global(self) -> list[str]:
        """Get globally frequent contractor names."""
        freq_limit = 5
        if self._config is not None:
            freq_limit = self._config.suggestions.frequent_limit
        records = self._recent_repo.get_frequent_global(limit=freq_limit)
        return [r.contractor_name for r in records]

    def _get_yesterday_contractors(self, date: str) -> list[str]:
        """Get contractor names from yesterday's report."""
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            yesterday = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return []

        report = self._report_repo.get_by_date(yesterday)
        if report is None or not report.items:
            return []

        seen: set[str] = set()
        names: list[str] = []
        for item in report.items:
            name = item.contractor.strip() if item.contractor else ""
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        return names

    def _get_all_contractors(self) -> list[Contractor]:
        """Get all contractors from the search service."""
        return self._search.get_all_contractors()

    def _get_config_max(self) -> int:
        """Get max_suggestions from config or default to 10."""
        if self._config is not None:
            return self._config.suggestions.max_suggestions
        return 10
