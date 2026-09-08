"""Tests for Historical Search. (mventor-ticket-018)"""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.models.historical_search import HistoricalSearchResult, HistoricalSearchMatch
from app.repositories.historical_search_repository import HistoricalSearchRepository
from app.services.historical_search_service import HistoricalSearchService


class TestHistoricalSearchResult:
    """Tests for the HistoricalSearchResult dataclass."""

    def test_creation_with_found_match(self):
        result = HistoricalSearchResult(
            found=True,
            date="2026-07-11",
            day="Saturday",
            zone="Zone A",
            workers=10,
            details="Masonry work",
            report_id=1,
            pdf_path="exports/pdf/2026-07-11.pdf",
            excel_path="exports/excel/2026-07-11.xlsx",
            contractor="Alpha Builders",
            contractor_code="ALP-001",
        )
        assert result.found is True
        assert result.date == "2026-07-11"
        assert result.workers == 10

    def test_creation_not_found_with_nearest(self):
        prev = HistoricalSearchResult(
            found=False,
            date="2026-07-10",
            workers=5,
        )
        result = HistoricalSearchResult(
            found=False,
            nearest_previous=prev,
            nearest_next=None,
        )
        assert result.found is False
        assert result.nearest_previous is not None
        assert result.nearest_previous.date == "2026-07-10"


class TestHistoricalSearchRepository:
    """Test suite for HistoricalSearchRepository."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_historical.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return HistoricalSearchRepository(db_manager)

    @pytest.fixture
    def seeded_reports(self, repo):
        """Seed test data with multiple reports and contractors."""
        # 2026-07-10 - Alpha Builders in Zone A
        r1 = Report(
            date="2026-07-10",
            day="Friday",
            status=ReportStatus.FINAL,
            telegram_user="u1",
            pdf_path="exports/2026-07-10.pdf",
        )
        r1.add_item(ReportItem(contractor="Alpha Builders", contractor_code="ALP-001", type="Civil", zone="Zone A", workers=10, details="Masonry"))

        # 2026-07-11 - Beta Electric in Zone B
        r2 = Report(
            date="2026-07-11",
            day="Saturday",
            status=ReportStatus.DRAFT,
            telegram_user="u2",
        )
        r2.add_item(ReportItem(contractor="Beta Electric", contractor_code="BET-002", type="Electrical", zone="Zone B", workers=5))
        r2.add_item(ReportItem(contractor="Gamma Roads", contractor_code="GAM-003", type="Civil", zone="Zone C", workers=8))

        # 2026-07-12 - Alpha Builders again in Zone B
        r3 = Report(
            date="2026-07-12",
            day="Sunday",
            status=ReportStatus.LOCKED,
            telegram_user="u1",
            pdf_path="exports/2026-07-12.pdf",
        )
        r3.add_item(ReportItem(contractor="Alpha Builders", contractor_code="ALP-001", zone="Zone B", workers=15))

        from app.repositories.report_repository import ReportRepository
        report_repo = ReportRepository(repo._db)
        report_repo.add(r1)
        report_repo.add(r2)
        report_repo.add(r3)

    def test_exact_date_match_returns_found(self, repo, seeded_reports):
        """Should find exact date match for existing contractor."""
        result = repo.search_contractor_on_date("Alpha Builders", "2026-07-10")
        assert result.found is True
        assert result.date == "2026-07-10"
        assert result.zone == "Zone A"
        assert result.workers == 10
        assert result.contractor == "Alpha Builders"

    def test_no_exact_match_returns_nearest_previous(self, repo, seeded_reports):
        """Should find nearest previous when date not found."""
        result = repo.search_contractor_on_date("Alpha Builders", "2026-07-11")
        assert result.found is False
        assert result.nearest_previous is not None
        assert result.nearest_previous.date == "2026-07-10"
        assert result.nearest_previous.workers == 10

    def test_no_previous_returns_nearest_next(self, repo, seeded_reports):
        """Should find nearest next when no previous exists."""
        result = repo.search_contractor_on_date("Alpha Builders", "2026-07-01")
        assert result.found is False
        assert result.nearest_previous is None
        assert result.nearest_next is not None
        assert result.nearest_next.date == "2026-07-10"

    def test_no_match_at_all(self, repo, seeded_reports):
        """Should return no results for unknown contractor."""
        result = repo.search_contractor_on_date("Unknown Contractor", "2026-07-10")
        assert result.found is False
        assert result.nearest_previous is None
        assert result.nearest_next is None

    def test_contractor_has_reports_found(self, repo, seeded_reports):
        """Should return True for existing contractor."""
        assert repo.contractor_has_reports("Alpha") is True
        assert repo.contractor_has_reports("Beta") is True
        assert repo.contractor_has_reports("Gamma") is True

    def test_contractor_has_reports_not_found(self, repo, seeded_reports):
        """Should return False for unknown contractor."""
        assert repo.contractor_has_reports("Unknown Contractor") is False

    def test_contractor_has_reports_empty_name(self, repo, seeded_reports):
        """Should return False for empty contractor name."""
        assert repo.contractor_has_reports("") is False
        assert repo.contractor_has_reports("   ") is False

    def test_partial_name_match(self, repo, seeded_reports):
        """Should match contractor by partial name."""
        result = repo.search_contractor_on_date("Alpha", "2026-07-10")
        assert result.found is True

    def test_case_insensitive_match(self, repo, seeded_reports):
        """Should match contractor case-insensitively."""
        result = repo.search_contractor_on_date("ALPHA BUILDERS", "2026-07-10")
        assert result.found is True
        # Case-insensitive search matches, returns found=True
        assert result.date == "2026-07-10"

    def test_find_nearest_previous_direct(self, repo, seeded_reports):
        """Should find nearest previous with dedicated method."""
        result = repo.find_nearest_previous("Alpha Builders", "2026-07-11")
        assert result is not None
        assert result.date == "2026-07-10"

    def test_find_nearest_next_direct(self, repo, seeded_reports):
        """Should find nearest next with dedicated method."""
        result = repo.find_nearest_next("Alpha Builders", "2026-07-01")
        assert result is not None
        # Finds 2026-07-10 as nearest next for Alpha Builders
        assert result.date == "2026-07-10"

    def test_find_nearest_previous_no_history(self, repo, seeded_reports):
        """Should return None when no previous history exists for exact name match."""
        # Query for contractor name that doesn't exist
        result = repo.find_nearest_previous("ZZZ Unknown", "2030-01-01")
        assert result is None

    def test_find_nearest_next_no_future(self, repo, seeded_reports):
        """Should return None when no future reports exist."""
        result = repo.find_nearest_next("Alpha", "2030-01-01")
        assert result is None


class TestHistoricalSearchService:
    """Test suite for HistoricalSearchService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_historical_service.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def seeded_reports(self, db_manager: DatabaseManager):
        """Seed test data."""
        from app.repositories.report_repository import ReportRepository
        report_repo = ReportRepository(db_manager)

        r1 = Report(date="2026-07-10", day="Friday", status=ReportStatus.FINAL,
                    telegram_user="u1")
        r1.add_item(ReportItem(contractor="Test Contractor", zone="Zone A", workers=10))
        report_repo.add(r1)

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return HistoricalSearchRepository(db_manager)

    @pytest.fixture
    def service(self, repo: HistoricalSearchRepository):
        return HistoricalSearchService(repo)

    def test_service_search_contractor_on_date(
        self, service: HistoricalSearchService, seeded_reports
    ):
        result = service.search_contractor_on_date("Test Contractor", "2026-07-10")
        assert result.found is True
        assert result.date == "2026-07-10"
        assert result.workers == 10

    def test_service_find_nearest_previous(
        self, service: HistoricalSearchService, seeded_reports
    ):
        result = service.find_nearest_previous("Test Contractor", "2026-07-11")
        assert result is not None
        assert result.date == "2026-07-10"

    def test_service_find_nearest_next(
        self, service: HistoricalSearchService, seeded_reports
    ):
        result = service.find_nearest_next("Unknown", "2026-07-01")
        # Unknown contractor has no history
        assert result is None

    def test_service_contractor_has_reports(
        self, service: HistoricalSearchService, seeded_reports
    ):
        assert service.contractor_has_reports("Test Contractor") is True
        assert service.contractor_has_reports("Unknown") is False