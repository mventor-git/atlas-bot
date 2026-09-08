"""
Tests for ContractorProfileService. (mventor-ticket-017)

Covers:
- Profile displays correct name, type, code
- First/last appearance dates correct
- Total reports/workers/average correct
- Favorite zones identified correctly
- Monthly activity listed correctly
- Unknown contractor returns None
- Empty name returns None
- No-tables-reader fallback works
- Code looked up from most recent report
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.models.contractor_profile import ContractorProfile
from app.repositories.report_repository import ReportRepository
from app.services.contractor_profile_service import ContractorProfileService
from app.services.tables_reader import TablesReaderService


class TestContractorProfileService:
    """Test suite for ContractorProfileService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_profile.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return ReportRepository(db_manager)

    def _seed_data(self, repo: ReportRepository) -> None:
        """Insert test reports with controlled data."""
        # Report 1: 2026-07-01 (Civil Co + Electric Inc)
        r1 = Report(date="2026-07-01", day="Ø§Ù„Ø§Ø±Ø¨Ø¹Ø§Ø¡", status=ReportStatus.FINAL)
        r1.add_item(ReportItem(contractor="Civil Co", type="Civil", zone="Zone A",
                                workers=10, details="5 Mason, 5 Helper"))
        r1.add_item(ReportItem(contractor="Electric Inc", type="Electrical", zone="Zone B",
                                workers=5, details="3 Electrician, 2 Helper"))
        repo.add(r1)

        # Report 2: 2026-07-05 (Civil Co + Mechanical Co)
        r2 = Report(date="2026-07-05", day="Ø§Ù„Ø§Ø­Ø¯", status=ReportStatus.FINAL)
        r2.add_item(ReportItem(contractor="Civil Co", type="Civil", zone="Zone A",
                                workers=12, details="6 Mason, 6 Helper"))
        r2.add_item(ReportItem(contractor="Mechanical Co", type="Mechanical", zone="Zone C",
                                workers=4, details="2 Tech, 2 Helper"))
        repo.add(r2)

        # Report 3: 2026-07-10 (Civil Co + Civil Co in Zone B)
        r3 = Report(date="2026-07-10", day="Ø§Ù„Ø®Ù…ÙŠØ³", status=ReportStatus.FINAL)
        r3.add_item(ReportItem(contractor="Civil Co", type="Civil", zone="Zone B",
                                workers=8, details="4 Mason, 4 Helper"))
        r3.add_item(ReportItem(contractor="Civil Co", type="Civil", zone="Zone A",
                                workers=6, details="3 Mason, 3 Helper"))
        repo.add(r3)

        # Report 4: 2026-07-15 (Civil Co + Electric Inc with contractor_code)
        r4 = Report(date="2026-07-15", day="Ø§Ù„Ø«Ù„Ø§Ø«Ø§Ø¡", status=ReportStatus.LOCKED)
        r4.add_item(ReportItem(contractor="Civil Co", type="Civil", zone="Zone A",
                                workers=10, details="5 Mason, 5 Helper",
                                contractor_code="CIV-001"))
        r4.add_item(ReportItem(contractor="Electric Inc", type="Electrical", zone="Zone B",
                                workers=6, details="3 Electrician, 3 Helper",
                                contractor_code="ELEC-001"))
        repo.add(r4)

        # Report 5: no_report status â€” should be excluded from stats
        r5 = Report(date="2026-07-20", day="Ø§Ù„Ø§Ø«Ù†ÙŠÙ†", status=ReportStatus.NO_REPORT)
        r5.add_item(ReportItem(contractor="Civil Co", zone="Zone A", workers=5))
        repo.add(r5)

    # ------------------------------------------------------------------
    # Basic profile tests
    # ------------------------------------------------------------------

    def test_profile_name_and_type(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return correct contractor name and type from tables reader."""
        self._seed_data(repo)

        # Mock tables reader
        mock_tables = MagicMock(spec=TablesReaderService)
        mock_contractor = MagicMock()
        mock_contractor.type = "Civil"
        mock_tables.get_contractor_by_name.return_value = mock_contractor

        service = ContractorProfileService(db_manager, tables_reader=mock_tables)
        profile = service.get_profile("Civil Co")

        assert profile is not None
        assert profile.name == "Civil Co"
        assert profile.type == "Civil"

    def test_profile_without_tables_reader(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should work without tables reader (type/code = None)."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)
        profile = service.get_profile("Civil Co")

        assert profile is not None
        assert profile.name == "Civil Co"
        assert profile.type is None  # No tables reader available

    def test_unknown_contractor_returns_none(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return None for a contractor with no appearances."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)
        profile = service.get_profile("Nonexistent Contractor")

        assert profile is None

    def test_empty_name_returns_none(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return None for empty contractor name."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)
        assert service.get_profile("") is None
        assert service.get_profile("   ") is None

    # ------------------------------------------------------------------
    # Date tests
    # ------------------------------------------------------------------

    def test_first_appearance(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return the earliest date the contractor appeared."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)
        profile = service.get_profile("Electric Inc")

        assert profile is not None
        assert profile.first_appearance == "2026-07-01"

    def test_last_appearance(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return the most recent date the contractor appeared."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)
        profile = service.get_profile("Electric Inc")

        assert profile is not None
        assert profile.last_appearance == "2026-07-15"

    # ------------------------------------------------------------------
    # Count tests
    # ------------------------------------------------------------------

    def test_total_reports(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should count distinct reports the contractor appears in."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)

        # Civil Co appears in 4 reports (r1, r2, r3, r4) - r5 is no_report
        profile = service.get_profile("Civil Co")
        assert profile is not None
        assert profile.total_reports == 4

        # Electric Inc appears in 2 reports (r1, r4)
        profile = service.get_profile("Electric Inc")
        assert profile is not None
        assert profile.total_reports == 2

        # Mechanical Co appears in 1 report (r2)
        profile = service.get_profile("Mechanical Co")
        assert profile is not None
        assert profile.total_reports == 1

    def test_total_workers(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should sum all workers across all appearances."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)

        # Civil Co: 10 + 12 + 8 + 6 + 10 = 46 (r5 excluded as no_report)
        profile = service.get_profile("Civil Co")
        assert profile is not None
        assert profile.total_workers == 46

        # Electric Inc: 5 + 6 = 11
        profile = service.get_profile("Electric Inc")
        assert profile is not None
        assert profile.total_workers == 11

    def test_average_workers(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should calculate average workers correctly."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)

        # Mechanical Co: 4 workers / 1 report = 4.0
        profile = service.get_profile("Mechanical Co")
        assert profile is not None
        assert profile.avg_workers == 4.0

        # Electric Inc: 11 workers / 2 reports = 5.5
        profile = service.get_profile("Electric Inc")
        assert profile is not None
        assert profile.avg_workers == 5.5

    # ------------------------------------------------------------------
    # Favorite zones
    # ------------------------------------------------------------------

    def test_favorite_zones(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return top zones by frequency."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)

        # Civil Co zones: Zone A (r1, r2, r3-2nd, r4), Zone B (r3-1st) = 4x Zone A, 1x Zone B
        profile = service.get_profile("Civil Co")
        assert profile is not None
        assert len(profile.favorite_zones) == 2
        assert profile.favorite_zones[0] == ("Zone A", 4)
        assert profile.favorite_zones[1] == ("Zone B", 1)

    def test_favorite_zones_limit(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should limit favorite zones to top 3."""
        self._seed_data(repo)

        # Add a report with many zones for Civil Co
        r = Report(date="2026-07-25", day="Ø§Ù„Ø³Ø¨Øª", status=ReportStatus.FINAL)
        r.add_item(ReportItem(contractor="Civil Co", zone="Zone X", workers=5))
        r.add_item(ReportItem(contractor="Civil Co", zone="Zone Y", workers=5))
        r.add_item(ReportItem(contractor="Civil Co", zone="Zone Z", workers=5))
        repo.add(r)

        service = ContractorProfileService(db_manager)
        profile = service.get_profile("Civil Co")
        assert profile is not None
        assert len(profile.favorite_zones) <= 3

    # ------------------------------------------------------------------
    # Monthly activity
    # ------------------------------------------------------------------

    def test_monthly_activity(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return monthly worker totals sorted by month."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)

        # Civil Co: July 2026 only â€” 46 workers across 4 reports
        profile = service.get_profile("Civil Co")
        assert profile is not None
        assert len(profile.monthly_activity) >= 1
        month, workers = profile.monthly_activity[0]
        assert month == "2026-07"
        assert workers == 46

    def test_monthly_activity_multiple_months(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should aggregate across multiple months."""
        self._seed_data(repo)

        # Add another report in a different month for Civil Co
        from app.repositories.report_repository import ReportRepository
        repo2 = ReportRepository(db_manager)
        r = Report(date="2026-06-15", day="Ø§Ù„Ø§Ø«Ù†ÙŠÙ†", status=ReportStatus.FINAL)
        r.add_item(ReportItem(contractor="Civil Co", zone="Zone A", workers=20))
        repo2.add(r)

        service = ContractorProfileService(db_manager)
        profile = service.get_profile("Civil Co")
        assert profile is not None

        months = {m: w for m, w in profile.monthly_activity}
        assert "2026-06" in months
        assert "2026-07" in months
        assert months["2026-06"] == 20

    # ------------------------------------------------------------------
    # Contractor code
    # ------------------------------------------------------------------

    def test_contractor_code_from_most_recent(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should get the most recent contractor_code."""
        self._seed_data(repo)

        mock_tables = MagicMock(spec=TablesReaderService)
        mock_contractor = MagicMock()
        mock_contractor.type = "Civil"
        mock_tables.get_contractor_by_name.return_value = mock_contractor

        service = ContractorProfileService(db_manager, tables_reader=mock_tables)
        profile = service.get_profile("Civil Co")
        assert profile is not None
        # Most recent report (2026-07-15) has contractor_code "CIV-001"
        assert profile.code == "CIV-001"

    def test_contractor_code_none_when_missing(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should return None for code when no entries have a code."""
        self._seed_data(repo)

        mock_tables = MagicMock(spec=TablesReaderService)
        mock_contractor = MagicMock()
        mock_contractor.type = "Mechanical"
        mock_tables.get_contractor_by_name.return_value = mock_contractor

        service = ContractorProfileService(db_manager, tables_reader=mock_tables)
        profile = service.get_profile("Mechanical Co")
        assert profile is not None
        # Mechanical Co has no contractor_code in seed data
        assert profile.code is None

    # ------------------------------------------------------------------
    # Case insensitivity
    # ------------------------------------------------------------------

    def test_case_insensitive_match(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should match contractor names case-insensitively."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)

        profile_upper = service.get_profile("CIVIL CO")
        profile_lower = service.get_profile("civil co")
        profile_mixed = service.get_profile("Civil Co")

        assert profile_upper is not None
        assert profile_lower is not None
        assert profile_mixed is not None
        assert profile_upper.total_workers == profile_lower.total_workers == profile_mixed.total_workers

    # ------------------------------------------------------------------
    # no_report exclusion
    # ------------------------------------------------------------------

    def test_excludes_no_report_status(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Should exclude reports with no_report status from statistics."""
        self._seed_data(repo)

        service = ContractorProfileService(db_manager)

        # Civil Co should NOT include r5 (no_report, 5 workers)
        profile = service.get_profile("Civil Co")
        assert profile is not None
        assert profile.total_reports == 4  # Not 5
        assert profile.total_workers == 46  # Not 51
