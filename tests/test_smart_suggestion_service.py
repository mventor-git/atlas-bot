"""
Tests for SmartSuggestionService. (mventor-ticket-009)

Covers:
- Suggestions ordered by: Favorites > Recent > Frequent > Yesterday's > All
- Deduplication across sources
- Max results limit
- Empty scenarios (no recent, no favorites, no yesterday)
- Fallback when no data matches
- Frequent contractor ranking (global)
- Yesterday's contractors extracted correctly
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
import pytest

from app.database.manager import DatabaseManager
from app.models.config import AppConfig
from app.models.database import (
    Contractor,
    Report,
    ReportItem,
    ReportStatus,
)
from app.repositories.favorites_repository import FavoritesRepository
from app.repositories.recent_contractor_repository import RecentContractorRepository
from app.repositories.report_repository import ReportRepository
from app.services.contractor_search import ContractorSearchService
from app.services.smart_suggestion_service import SmartSuggestionService


def create_test_tables(file_path: Path) -> None:
    """Create a minimal test tables.xlsx file with known contractors."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "tblContractor"
    ws["A1"] = "Contractor"
    ws["B1"] = "Type"
    contractors = [
        ("Civil Works Company", "Civil"),
        ("Electrical Solutions Ltd", "Electrical"),
        ("Plumbing Masters", "Plumbing"),
        ("Steel Fabricators", "Steel"),
        ("Painters Pro", "Painting"),
        ("General Contracting Co", "General"),
        ("HVAC Experts", "HVAC"),
        ("Landscaping Services", "Landscaping"),
    ]
    for i, (name, ctype) in enumerate(contractors, 2):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=ctype)

    ws_zones = wb.create_sheet("tblZones")
    ws_zones["A1"] = "Zone"
    for i, zone in enumerate(["Zone A", "Zone B", "Zone C"], 2):
        ws_zones.cell(row=i, column=1, value=zone)

    wb.save(str(file_path))
    wb.close()


class TestSmartSuggestionService:
    """Test suite for SmartSuggestionService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_suggestions.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def tables_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "tables.xlsx"
            create_test_tables(path)
            yield path

    @pytest.fixture
    def config(self, tables_path: Path):
        """Create AppConfig pointing to the test tables file."""
        return AppConfig(**{
            "template": {"file": "t.xlsx", "tables_file": str(tables_path)},
            "date": {"cell": "B4", "day_cell": "D4"},
            "table": {"start_row": 12, "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"}},
            "output": {"pdf_folder": "exports", "excel_folder": "exports"},
            "database": {"path": "test.db"},
            "history": {"file": "h.xlsx"},
            "logging": {"file": "l.log", "level": "INFO", "max_bytes": 1024, "backup_count": 1},
            "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
            "suggestions": {"max_suggestions": 10, "recent_days": 30, "frequent_limit": 5},
        })

    @pytest.fixture
    def recent_repo(self, db_manager: DatabaseManager):
        return RecentContractorRepository(db_manager)

    @pytest.fixture
    def favorites_repo(self, db_manager: DatabaseManager):
        return FavoritesRepository(db_manager)

    @pytest.fixture
    def report_repo(self, db_manager: DatabaseManager):
        return ReportRepository(db_manager)

    @pytest.fixture
    def search_service(self, config: AppConfig, recent_repo: RecentContractorRepository):
        svc = ContractorSearchService(config, recent_repo)
        svc.load_tables()
        return svc

    @pytest.fixture
    def suggestion_service(
        self,
        search_service: ContractorSearchService,
        recent_repo: RecentContractorRepository,
        favorites_repo: FavoritesRepository,
        report_repo: ReportRepository,
        config: AppConfig,
    ):
        return SmartSuggestionService(search_service, recent_repo, favorites_repo, report_repo, config)

    @pytest.fixture
    def today(self) -> str:
        return "2026-07-11"

    @pytest.fixture
    def yesterday(self) -> str:
        return "2026-07-10"

    # ------------------------------------------------------------------
    # Priority ordering
    # ------------------------------------------------------------------

    def test_favorites_at_top(
        self, suggestion_service: SmartSuggestionService,
        favorites_repo: FavoritesRepository, today: str
    ):
        """Favorites should appear first in suggestions (most recent favorite first)."""
        favorites_repo.add_favorite("user1", "Plumbing Masters")
        favorites_repo.add_favorite("user1", "Steel Fabricators")

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        names = [c.name for c in suggestions]
        # Favorites ordered by created_at DESC (most recent first)
        assert names[0] == "Steel Fabricators", f"Expected Steel Fabricators first, got {names}"
        assert names[1] == "Plumbing Masters", f"Expected Plumbing Masters second, got {names}"

    def test_recent_after_favorites(
        self, suggestion_service: SmartSuggestionService,
        favorites_repo: FavoritesRepository,
        recent_repo: RecentContractorRepository, today: str
    ):
        """Recently used should appear after favorites."""
        favorites_repo.add_favorite("user1", "Civil Works Company")
        recent_repo.record_usage("user1", "Electrical Solutions Ltd")
        recent_repo.record_usage("user1", "Plumbing Masters")

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        names = [c.name for c in suggestions]
        # Civil (favorite) should be among the first entries (before non-favorites)
        assert names[0] == "Civil Works Company", f"Expected Civil Works first, got {names}"
        # Favorite comes first, then recent names follow
        assert "Electrical Solutions Ltd" in names
        assert "Plumbing Masters" in names

    def test_yesterday_appears(
        self, suggestion_service: SmartSuggestionService,
        report_repo: ReportRepository, today: str, yesterday: str
    ):
        """Yesterday's contractors should appear in suggestions."""
        report = Report(date=yesterday, day="Ø§Ù„Ø®Ù…ÙŠØ³", status=ReportStatus.DRAFT)
        report.add_item(ReportItem(contractor="HVAC Experts", workers=5))
        report.add_item(ReportItem(contractor="Painters Pro", workers=3))
        report_repo.add(report)

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        names = [c.name for c in suggestions]
        assert "HVAC Experts" in names
        assert "Painters Pro" in names

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def test_deduplication(
        self, suggestion_service: SmartSuggestionService,
        favorites_repo: FavoritesRepository,
        recent_repo: RecentContractorRepository,
        report_repo: ReportRepository, today: str, yesterday: str
    ):
        """Same contractor appearing in multiple sources should not duplicate."""
        # Add as favorite
        favorites_repo.add_favorite("user1", "Civil Works Company")
        # Also recent
        recent_repo.record_usage("user1", "Civil Works Company")
        # Also in yesterday's report
        report = Report(date=yesterday, day="Ø§Ù„Ø®Ù…ÙŠØ³", status=ReportStatus.DRAFT)
        report.add_item(ReportItem(contractor="Civil Works Company", workers=10))
        report_repo.add(report)

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=10)
        names = [c.name for c in suggestions]
        assert names.count("Civil Works Company") == 1, "Should be deduplicated"

    # ------------------------------------------------------------------
    # Max results
    # ------------------------------------------------------------------

    def test_max_results_limit(
        self, suggestion_service: SmartSuggestionService,
        favorites_repo: FavoritesRepository,
        recent_repo: RecentContractorRepository, today: str
    ):
        """Should respect max_results limit."""
        # Add 3 favorites
        favorites_repo.add_favorite("user1", "Civil Works Company")
        favorites_repo.add_favorite("user1", "Electrical Solutions Ltd")
        favorites_repo.add_favorite("user1", "Plumbing Masters")
        # Add 3 recent
        recent_repo.record_usage("user1", "Steel Fabricators")
        recent_repo.record_usage("user1", "Painters Pro")
        recent_repo.record_usage("user1", "General Contracting Co")

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=3)
        assert len(suggestions) == 3

    def test_max_results_from_config(
        self, search_service: ContractorSearchService,
        recent_repo: RecentContractorRepository,
        favorites_repo: FavoritesRepository,
        report_repo: ReportRepository,
        config: AppConfig, today: str
    ):
        """Should use config max_results when not explicitly passed."""
        svc = SmartSuggestionService(search_service, recent_repo, favorites_repo, report_repo, config)
        # config has max_suggestions=10
        suggestions = svc.get_suggestions("user1", today)
        assert len(suggestions) <= 10

    # ------------------------------------------------------------------
    # Empty / edge cases
    # ------------------------------------------------------------------

    def test_empty_suggestions(
        self, suggestion_service: SmartSuggestionService, today: str
    ):
        """Should return empty list when user has no history and max_results=0."""
        suggestions = suggestion_service.get_suggestions("user1", today, max_results=0)
        assert len(suggestions) == 0

    def test_empty_suggestions_returns_all_fallback(
        self, suggestion_service: SmartSuggestionService, today: str
    ):
        """Should fill suggestions with all contractors when no history exists."""
        suggestions = suggestion_service.get_suggestions("user1", today, max_results=3)
        assert len(suggestions) == 3
        # Should be from the full contractor list
        names = [c.name for c in suggestions]
        assert "Civil Works Company" in names

    def test_suggestions_with_no_favorites(
        self, suggestion_service: SmartSuggestionService,
        recent_repo: RecentContractorRepository, today: str
    ):
        """Should work when user has no favorites but has recent activity."""
        recent_repo.record_usage("user1", "Plumbing Masters")
        recent_repo.record_usage("user1", "HVAC Experts")

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        names = [c.name for c in suggestions]
        assert "Plumbing Masters" in names
        assert "HVAC Experts" in names

    def test_suggestions_empty_recent_and_favorites(
        self, suggestion_service: SmartSuggestionService, today: str
    ):
        """Should not crash when user has no recent and no favorites."""
        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        assert len(suggestions) == 5  # Fills from all contractors

    def test_suggestions_with_no_yesterday_report(
        self, suggestion_service: SmartSuggestionService, today: str
    ):
        """Should not crash when there's no report for yesterday."""
        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        assert len(suggestions) == 5

    def test_invalid_date_format(
        self, suggestion_service: SmartSuggestionService, today: str
    ):
        """Should handle invalid date gracefully."""
        suggestions = suggestion_service.get_suggestions("user1", "not-a-date", max_results=3)
        assert len(suggestions) == 3  # Falls back to all contractors

    # ------------------------------------------------------------------
    # Frequent (global) ranking
    # ------------------------------------------------------------------

    def test_frequent_global_appears(
        self, suggestion_service: SmartSuggestionService,
        recent_repo: RecentContractorRepository, today: str
    ):
        """Frequently used contractors (across all users) should appear."""
        # User2 uses a contractor many times
        for _ in range(5):
            recent_repo.record_usage("user2", "Landscaping Services")

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=10)
        names = [c.name for c in suggestions]
        assert "Landscaping Services" in names, "Frequent global contractor should appear"

    def test_frequent_ranked_above_all_others(
        self, suggestion_service: SmartSuggestionService,
        recent_repo: RecentContractorRepository, today: str
    ):
        """Frequently used should appear before 'all others' filler."""
        recent_repo.record_usage("user2", "HVAC Experts")
        recent_repo.record_usage("user2", "HVAC Experts")
        recent_repo.record_usage("user2", "HVAC Experts")

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=10)
        names = [c.name for c in suggestions]
        # HVAC should appear (it's frequent globally)
        hvac_idx = names.index("HVAC Experts") if "HVAC Experts" in names else 999

        # Now get the ordering - isolate names without favorites/recent/yesterday
        # Since user1 has no history, the order should be: frequent > all others
        # Find Landscaping Services (which has no usage)
        land_idx = names.index("Landscaping Services") if "Landscaping Services" in names else 999
        assert hvac_idx < land_idx, "Frequent should rank above all others"

    # ------------------------------------------------------------------
    # Recent contractor ordering (by last_used)
    # ------------------------------------------------------------------

    def test_recent_ordered_by_last_used(
        self, suggestion_service: SmartSuggestionService,
        recent_repo: RecentContractorRepository, today: str
    ):
        """Recently used contractors should be ordered by last_used."""
        recent_repo.record_usage("user1", "Plumbing Masters")
        import time
        time.sleep(0.01)
        recent_repo.record_usage("user1", "Civil Works Company")

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        names = [c.name for c in suggestions]
        # Civil Works was used more recently
        civil_idx = names.index("Civil Works Company")
        plumbing_idx = names.index("Plumbing Masters")
        assert civil_idx < plumbing_idx, "More recent should come first"

    # ------------------------------------------------------------------
    # Yesterday's contractor ordering
    # ------------------------------------------------------------------

    def test_yesterday_deduplicated_with_favorites(
        self, suggestion_service: SmartSuggestionService,
        favorites_repo: FavoritesRepository,
        report_repo: ReportRepository, today: str, yesterday: str
    ):
        """Yesterday's contractors should be deduplicated if already in favorites."""
        favorites_repo.add_favorite("user1", "HVAC Experts")

        report = Report(date=yesterday, day="Ø§Ù„Ø®Ù…ÙŠØ³", status=ReportStatus.DRAFT)
        report.add_item(ReportItem(contractor="HVAC Experts", workers=5))
        report.add_item(ReportItem(contractor="Painters Pro", workers=3))
        report_repo.add(report)

        suggestions = suggestion_service.get_suggestions("user1", today, max_results=5)
        names = [c.name for c in suggestions]
        # HVAC Experts appears once (from favorites)
        assert names.count("HVAC Experts") == 1
        # Painters Pro appears (from yesterday)
        assert "Painters Pro" in names
