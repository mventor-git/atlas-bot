"""Repository implementations for Labor-Report.

Implements the repository pattern for all data access.
"""

from app.repositories.base import BaseRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.recent_contractor_repository import RecentContractorRepository
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.event_log_repository import (  # noqa: F401 — event constants
    EVENT_REPORT_CREATED, EVENT_REPORT_DELETED, EVENT_REPORT_FINALIZED,
    EVENT_REPORT_LOCKED, EVENT_REPORT_UNLOCKED, EVENT_DRAFT_SAVED,
    EVENT_CONTRACTOR_ADDED,
    EVENT_CONTRACTOR_REMOVED, EVENT_WORKERS_CHANGED, EVENT_ZONE_CHANGED,
    EVENT_DETAILS_CHANGED, EVENT_PDF_GENERATED, EVENT_VERSION_CREATED,
    EVENT_VERSION_RESTORED, EVENT_FAVORITE_ADDED, EVENT_FAVORITE_REMOVED,
)
from app.repositories.version_repository import VersionRepository
from app.repositories.favorites_repository import FavoritesRepository
from app.repositories.stats_cache_repository import StatsCacheRepository
from app.repositories.search_repository import SearchRepository
from app.repositories.historical_search_repository import HistoricalSearchRepository
from app.repositories.contractor_timeline_repository import (
    ContractorTimelineRepository,
)
from app.repositories.contractor_repository import ContractorRepository

__all__ = [
    "BaseRepository",
    "ReportRepository",
    "RecentContractorRepository",
    "EventLogRepository",
    "VersionRepository",
    "FavoritesRepository",
    "StatsCacheRepository",
    "SearchRepository",
    "HistoricalSearchRepository",
    "ContractorTimelineRepository",
    "ContractorRepository",
]
