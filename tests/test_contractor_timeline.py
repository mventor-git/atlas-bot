"""Tests for Contractor Timeline. (mventor-ticket-016)

Tests cover:
- TimelineEntry dataclass creation and defaults
- TimelineResult dataclass pagination properties
- Repository returns paginated entries ordered by date descending
- Repository handles empty history gracefully
- Repository handles unknown contractors
- Repository handles whitespace/empty contractor names
- Service delegates correctly to repository
- Service convenience methods work
- Multiple items on same date are included
- PDF/Excel paths included in entries
"""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.models.contractor_timeline import TimelineEntry, TimelineResult
from app.repositories.contractor_timeline_repository import (
    ContractorTimelineRepository,
)
from app.services.contractor_timeline_service import ContractorTimelineService


class TestTimelineEntry:
    """Tests for the TimelineEntry dataclass."""

    def test_creation_with_all_fields(self):
        """Should create entry with all fields provided."""
        entry = TimelineEntry(
            date="2026-07-11",
            day="Saturday",
            workers=10,
            zone="Zone A",
            details="Masonry work",
            contractor_code="ALP-001",
            report_id=1,
            report_status="final",
            pdf_path="exports/pdf/2026-07-11.pdf",
            excel_path="exports/excel/2026-07-11.xlsx",
        )
        assert entry.date == "2026-07-11"
        assert entry.day == "Saturday"
        assert entry.workers == 10
        assert entry.zone == "Zone A"
        assert entry.details == "Masonry work"
        assert entry.contractor_code == "ALP-001"
        assert entry.report_id == 1
        assert entry.report_status == "final"
        assert entry.pdf_path == "exports/pdf/2026-07-11.pdf"
        assert entry.excel_path == "exports/excel/2026-07-11.xlsx"

    def test_creation_with_minimal_fields(self):
        """Should create entry with only required fields."""
        entry = TimelineEntry(date="2026-07-11", day="Saturday")
        assert entry.date == "2026-07-11"
        assert entry.day == "Saturday"
        assert entry.workers is None
        assert entry.zone is None
        assert entry.details is None
        assert entry.contractor_code is None
        assert entry.report_id is None
        assert entry.report_status is None
        assert entry.pdf_path is None
        assert entry.excel_path is None

    def test_creation_with_some_fields(self):
        """Should create entry with partial optional fields."""
        entry = TimelineEntry(
            date="2026-07-11",
            day="Saturday",
            workers=5,
            zone="Zone B",
            report_id=42,
        )
        assert entry.date == "2026-07-11"
        assert entry.day == "Saturday"
        assert entry.workers == 5
        assert entry.zone == "Zone B"
        assert entry.report_id == 42
        assert entry.details is None
        assert entry.contractor_code is None


class TestTimelineResult:
    """Tests for the TimelineResult dataclass."""

    def test_empty_result_defaults(self):
        """Empty result should have zero counts and no navigation."""
        result = TimelineResult(contractor="Test Co")
        assert result.contractor == "Test Co"
        assert result.entries == []
        assert result.total_entries == 0
        assert result.page == 1
        assert result.page_size == 10
        assert result.total_pages == 0
        assert result.has_previous is False
        assert result.has_next is False
        assert result.start_index == 0
        assert result.end_index == 0

    def test_pagination_properties_first_page(self):
        """First page should have has_previous=False, has_next=True if more."""
        entries = [TimelineEntry(date=f"2026-07-{d:02d}", day="") for d in range(1, 11)]
        result = TimelineResult(
            contractor="Test Co",
            entries=entries,
            total_entries=25,
            page=1,
            page_size=10,
            total_pages=3,
        )
        assert result.has_previous is False
        assert result.has_next is True
        assert result.start_index == 1
        assert result.end_index == 10

    def test_pagination_properties_middle_page(self):
        """Middle page should have both previous and next."""
        entries = [TimelineEntry(date=f"2026-07-{d:02d}", day="") for d in range(11, 21)]
        result = TimelineResult(
            contractor="Test Co",
            entries=entries,
            total_entries=25,
            page=2,
            page_size=10,
            total_pages=3,
        )
        assert result.has_previous is True
        assert result.has_next is True
        assert result.start_index == 11
        assert result.end_index == 20

    def test_pagination_properties_last_page(self):
        """Last page should have has_previous=True, has_next=False."""
        entries = [TimelineEntry(date=f"2026-07-{d:02d}", day="") for d in range(21, 26)]
        result = TimelineResult(
            contractor="Test Co",
            entries=entries,
            total_entries=25,
            page=3,
            page_size=10,
            total_pages=3,
        )
        assert result.has_previous is True
        assert result.has_next is False
        assert result.start_index == 21
        assert result.end_index == 25

    def test_single_page_no_navigation(self):
        """Single page should have no navigation."""
        entries = [TimelineEntry(date="2026-07-11", day="")]
        result = TimelineResult(
            contractor="Test Co",
            entries=entries,
            total_entries=1,
            page=1,
            page_size=10,
            total_pages=1,
        )
        assert result.has_previous is False
        assert result.has_next is False
        assert result.start_index == 1
        assert result.end_index == 1


class TestContractorTimelineRepository:
    """Test suite for ContractorTimelineRepository."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_timeline.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return ContractorTimelineRepository(db_manager)

    @pytest.fixture
    def seeded_reports(self, repo):
        """Seed test data with multiple reports and contractors across dates."""
        from app.repositories.report_repository import ReportRepository
        report_repo = ReportRepository(repo._db)

        # July 10 - Alpha Builders in Zone A
        r1 = Report(
            date="2026-07-10",
            day="Friday",
            status=ReportStatus.FINAL,
            telegram_user="u1",
            pdf_path="exports/pdf/2026-07-10.pdf",
            excel_path="exports/excel/2026-07-10.xlsx",
        )
        r1.add_item(ReportItem(
            contractor="Alpha Builders", contractor_code="ALP-001",
            type="Civil", zone="Zone A", workers=10, details="Masonry work",
        ))
        report_repo.add(r1)

        # July 11 - Alpha Builders (different zone) + Beta Electric
        r2 = Report(
            date="2026-07-11",
            day="Saturday",
            status=ReportStatus.DRAFT,
            telegram_user="u1",
        )
        r2.add_item(ReportItem(
            contractor="Alpha Builders", contractor_code="ALP-001",
            zone="Zone B", workers=15, details="Foundation work",
        ))
        r2.add_item(ReportItem(
            contractor="Beta Electric", contractor_code="BET-002",
            type="Electrical", zone="Zone B", workers=5,
        ))
        report_repo.add(r2)

        # July 12 - Alpha Builders (same zone as July 11)
        r3 = Report(
            date="2026-07-12",
            day="Sunday",
            status=ReportStatus.LOCKED,
            telegram_user="u1",
            pdf_path="exports/pdf/2026-07-12.pdf",
        )
        r3.add_item(ReportItem(
            contractor="Alpha Builders", contractor_code="ALP-001",
            zone="Zone B", workers=20, details="Concrete pouring",
        ))
        report_repo.add(r3)

        # July 14 - Gamma Roads (single appearance)
        r4 = Report(
            date="2026-07-14",
            day="Tuesday",
            status=ReportStatus.FINAL,
            telegram_user="u2",
        )
        r4.add_item(ReportItem(
            contractor="Gamma Roads", contractor_code="GAM-003",
            type="Civil", zone="Zone C", workers=8,
        ))
        report_repo.add(r4)

        # July 15 - no_report (should be excluded)
        r5 = Report(
            date="2026-07-15",
            day="Wednesday",
            status=ReportStatus.NO_REPORT,
            telegram_user="u1",
        )
        report_repo.add(r5)

    # --- Contractor exists ---

    def test_contractor_exists_true(self, repo, seeded_reports):
        """Should return True for existing contractor."""
        assert repo.contractor_exists("Alpha Builders") is True

    def test_contractor_exists_partial_match(self, repo, seeded_reports):
        """Should return True for partial name match."""
        assert repo.contractor_exists("Alpha") is True
        assert repo.contractor_exists("Beta") is True

    def test_contractor_exists_false(self, repo, seeded_reports):
        """Should return False for unknown contractor."""
        assert repo.contractor_exists("Unknown Contractor") is False

    def test_contractor_exists_empty_name(self, repo, seeded_reports):
        """Should return False for empty contractor name."""
        assert repo.contractor_exists("") is False
        assert repo.contractor_exists("   ") is False

    # --- Get timeline ---

    def test_timeline_returns_all_entries(self, repo, seeded_reports):
        """Should return all entries for a contractor ordered by date desc."""
        result = repo.get_timeline("Alpha Builders", page=1, page_size=10)
        assert result.total_entries == 3, "Expected 3 entries for Alpha Builders"
        assert len(result.entries) == 3, "Should return all 3 entries"
        # Dates should be descending
        assert result.entries[0].date == "2026-07-12"
        assert result.entries[1].date == "2026-07-11"
        assert result.entries[2].date == "2026-07-10"

    def test_timeline_returns_entry_data(self, repo, seeded_reports):
        """Should return full entry data for each timeline entry."""
        result = repo.get_timeline("Alpha Builders", page=1, page_size=10)
        assert len(result.entries) > 0
        entry = result.entries[0]
        assert entry.date is not None
        assert entry.day is not None
        assert entry.workers is not None
        assert entry.zone is not None
        assert entry.report_id is not None

    def test_timeline_includes_file_paths(self, repo, seeded_reports):
        """Should include PDF and Excel paths when available."""
        result = repo.get_timeline("Alpha Builders", page=1, page_size=10)
        # July 10 entry has pdf_path and excel_path
        july10 = [e for e in result.entries if e.date == "2026-07-10"]
        assert len(july10) == 1
        assert july10[0].pdf_path == "exports/pdf/2026-07-10.pdf"
        assert july10[0].excel_path == "exports/excel/2026-07-10.xlsx"

    def test_timeline_includes_report_status(self, repo, seeded_reports):
        """Should include report status for each entry."""
        result = repo.get_timeline("Alpha Builders", page=1, page_size=10)
        statuses = {e.date: e.report_status for e in result.entries}
        assert statuses["2026-07-10"] == "final"
        assert statuses["2026-07-11"] == "draft"
        assert statuses["2026-07-12"] == "locked"

    def test_timeline_includes_contractor_code(self, repo, seeded_reports):
        """Should include contractor code when available."""
        result = repo.get_timeline("Alpha Builders", page=1, page_size=10)
        for entry in result.entries:
            assert entry.contractor_code == "ALP-001", (
                f"Expected ALP-001 for {entry.date}, got {entry.contractor_code}"
            )

    def test_timeline_pagination_page_1(self, repo, seeded_reports):
        """Page 1 should return first page_size entries."""
        result = repo.get_timeline("Alpha Builders", page=1, page_size=2)
        assert len(result.entries) == 2
        assert result.total_entries == 3
        assert result.total_pages == 2
        assert result.page == 1
        # First page: most recent dates
        assert result.entries[0].date == "2026-07-12"
        assert result.entries[1].date == "2026-07-11"

    def test_timeline_pagination_page_2(self, repo, seeded_reports):
        """Page 2 should return remaining entries."""
        result = repo.get_timeline("Alpha Builders", page=2, page_size=2)
        assert len(result.entries) == 1
        assert result.total_entries == 3
        assert result.total_pages == 2
        assert result.page == 2
        assert result.entries[0].date == "2026-07-10"

    def test_timeline_pagination_out_of_range(self, repo, seeded_reports):
        """Page beyond range should clamp to last page."""
        result = repo.get_timeline("Alpha Builders", page=99, page_size=2)
        assert result.page == 2, "Should clamp to last page"
        assert len(result.entries) == 1
        assert result.entries[0].date == "2026-07-10"

    def test_timeline_pagination_below_one(self, repo, seeded_reports):
        """Page < 1 should clamp to page 1."""
        result = repo.get_timeline("Alpha Builders", page=0, page_size=2)
        assert result.page == 1
        assert len(result.entries) == 2

    def test_timeline_empty_for_unknown_contractor(self, repo, seeded_reports):
        """Should return empty result for unknown contractor."""
        result = repo.get_timeline("Unknown Contractor")
        assert result.total_entries == 0
        assert result.entries == []
        assert result.total_pages == 0

    def test_timeline_empty_for_empty_name(self, repo, seeded_reports):
        """Should return empty result for empty name."""
        result = repo.get_timeline("")
        assert result.total_entries == 0
        assert result.entries == []
        result = repo.get_timeline("   ")
        assert result.total_entries == 0
        assert result.entries == []

    def test_timeline_excludes_no_report_dates(self, repo, seeded_reports):
        """Should exclude dates with no_report status."""
        # July 15 is no_report - should not appear for any contractor
        result = repo.get_timeline("Alpha Builders", page=1, page_size=50)
        dates = [e.date for e in result.entries]
        assert "2026-07-15" not in dates

    # --- Get all contractor dates ---

    def test_get_all_dates_returns_sorted(self, repo, seeded_reports):
        """Should return all distinct dates sorted descending."""
        dates = repo.get_all_contractor_dates("Alpha Builders")
        assert len(dates) == 3
        assert dates == ["2026-07-12", "2026-07-11", "2026-07-10"]

    def test_get_all_dates_single_entry(self, repo, seeded_reports):
        """Should return single date for contractor with one appearance."""
        dates = repo.get_all_contractor_dates("Gamma Roads")
        assert len(dates) == 1
        assert dates[0] == "2026-07-14"

    def test_get_all_dates_empty_for_unknown(self, repo, seeded_reports):
        """Should return empty list for unknown contractor."""
        dates = repo.get_all_contractor_dates("Unknown")
        assert dates == []

    def test_get_all_dates_empty_for_empty_name(self, repo, seeded_reports):
        """Should return empty list for empty name."""
        assert repo.get_all_contractor_dates("") == []
        assert repo.get_all_contractor_dates("   ") == []


class TestContractorTimelineService:
    """Test suite for ContractorTimelineService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_timeline_service.db"

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

        r1 = Report(
            date="2026-07-10", day="Friday",
            status=ReportStatus.FINAL, telegram_user="u1",
        )
        r1.add_item(ReportItem(contractor="Test Contractor", zone="Zone A", workers=10))
        report_repo.add(r1)

        r2 = Report(
            date="2026-07-11", day="Saturday",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        r2.add_item(ReportItem(contractor="Test Contractor", zone="Zone B", workers=15))
        report_repo.add(r2)

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return ContractorTimelineRepository(db_manager)

    @pytest.fixture
    def service(self, repo: ContractorTimelineRepository):
        return ContractorTimelineService(repo)

    def test_get_timeline_delegates_to_repo(self, service, seeded_reports):
        """Should delegate to repository and return TimelineResult."""
        result = service.get_timeline("Test Contractor")
        assert isinstance(result, TimelineResult)
        assert result.total_entries == 2
        assert len(result.entries) == 2

    def test_get_timeline_with_custom_page_size(self, service, seeded_reports):
        """Should use custom page size when provided."""
        result = service.get_timeline("Test Contractor", page=1, page_size=1)
        assert len(result.entries) == 1
        assert result.total_pages == 2

    def test_get_timeline_default_page_size(self, service, seeded_reports):
        """Should use default page size when not provided."""
        result = service.get_timeline("Test Contractor")
        assert result.page_size == ContractorTimelineService.DEFAULT_PAGE_SIZE

    def test_get_all_contractor_dates(self, service, seeded_reports):
        """Should return all dates for a contractor."""
        dates = service.get_all_contractor_dates("Test Contractor")
        assert len(dates) == 2
        assert "2026-07-10" in dates
        assert "2026-07-11" in dates

    def test_contractor_exists_true(self, service, seeded_reports):
        """Should return True for existing contractor."""
        assert service.contractor_exists("Test Contractor") is True

    def test_contractor_exists_false(self, service, seeded_reports):
        """Should return False for unknown contractor."""
        assert service.contractor_exists("Unknown") is False

    def test_get_entry_count(self, service, seeded_reports):
        """Should return correct total entry count."""
        count = service.get_entry_count("Test Contractor")
        assert count == 2

    def test_get_entry_count_zero(self, service, seeded_reports):
        """Should return 0 for unknown contractor."""
        count = service.get_entry_count("Unknown")
        assert count == 0
