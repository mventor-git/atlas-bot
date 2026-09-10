"""Tests for Daily Comparison Service. (mventor-ticket-019)"""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.comparison import ComparisonResult
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.daily_comparison_service import (
    DailyComparisonService,
    DailyComparisonError,
)


class TestComparisonResult:
    """Tests for the ComparisonResult dataclass."""

    def test_creation_with_differences(self):
        """Should create a ComparisonResult with added/removed/changed contractors."""
        result = ComparisonResult(
            date_a="2026-07-10",
            date_b="2026-07-11",
            contractor_count_a=3,
            contractor_count_b=4,
            worker_count_a=20,
            worker_count_b=30,
            added_contractors=[("Gamma Roads", 8)],
            removed_contractors=[("Delta Pipes", 5)],
            changed_workers=[("Alpha Builders", 10, 15)],
            status_a="final",
            status_b="draft",
        )
        assert result.date_a == "2026-07-10"
        assert result.date_b == "2026-07-11"
        assert result.contractor_count_a == 3
        assert result.contractor_count_b == 4
        assert result.worker_count_a == 20
        assert result.worker_count_b == 30
        assert result.has_differences is True

    def test_identical_reports_have_no_differences(self):
        """Should report no differences for identical reports."""
        result = ComparisonResult(
            date_a="2026-07-10",
            date_b="2026-07-10",
            contractor_count_a=2,
            contractor_count_b=2,
            worker_count_a=15,
            worker_count_b=15,
        )
        assert result.has_differences is False
        assert result.contractor_count_diff == 0
        assert result.worker_count_diff == 0

    def test_contractor_count_diff(self):
        """Should compute correct contractor count difference."""
        result = ComparisonResult(
            date_a="2026-07-10",
            date_b="2026-07-11",
            contractor_count_a=5,
            contractor_count_b=3,
        )
        assert result.contractor_count_diff == -2

    def test_worker_count_diff(self):
        """Should compute correct worker count difference."""
        result = ComparisonResult(
            date_a="2026-07-10",
            date_b="2026-07-11",
            worker_count_a=10,
            worker_count_b=25,
        )
        assert result.worker_count_diff == 15

    def test_format_summary_with_added_removed_changed(self):
        """Should format a readable summary with all diff types."""
        result = ComparisonResult(
            date_a="2026-07-10",
            date_b="2026-07-11",
            contractor_count_a=2,
            contractor_count_b=3,
            worker_count_a=15,
            worker_count_b=23,
            added_contractors=[("Gamma Roads", 8)],
            removed_contractors=[("Delta Pipes", 5)],
            changed_workers=[("Alpha Builders", 10, 15)],
            status_a="final",
            status_b="draft",
        )
        summary = result.format_summary()
        assert "Daily Report Comparison" in summary
        assert "2026-07-10" in summary
        assert "2026-07-11" in summary
        assert "Gamma Roads" in summary
        assert "Delta Pipes" in summary
        assert "Alpha Builders" in summary
        assert "Added" in summary
        assert "Removed" in summary
        assert "Changed" in summary
        assert "No differences" not in summary

    def test_format_summary_identical_reports(self):
        """Should show 'no differences' for identical reports."""
        result = ComparisonResult(
            date_a="2026-07-10",
            date_b="2026-07-10",
            contractor_count_a=2,
            contractor_count_b=2,
            worker_count_a=15,
            worker_count_b=15,
        )
        summary = result.format_summary()
        assert "No differences" in summary
        assert "identical" in summary


class TestDailyComparisonService:
    """Test suite for DailyComparisonService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_comparison.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def report_repo(self, db_manager: DatabaseManager):
        return ReportRepository(db_manager)

    @pytest.fixture
    def service(self, report_repo: ReportRepository):
        return DailyComparisonService(report_repo)

    @pytest.fixture
    def seeded_two_reports(self, report_repo: ReportRepository):
        """Seed two reports with different contractors for comparison."""
        # Report A (2026-07-10): 3 contractors
        r_a = Report(
            date="2026-07-10",
            day="Friday",
            status=ReportStatus.FINAL,
            telegram_user="u1",
        )
        r_a.add_item(ReportItem(contractor="Alpha Builders", type="Civil", zone="Zone A", workers=10, details="Masonry"))
        r_a.add_item(ReportItem(contractor="Beta Electric", type="Electrical", zone="Zone B", workers=5))
        r_a.add_item(ReportItem(contractor="Delta Pipes", type="Plumbing", zone="Zone C", workers=3))
        report_repo.add(r_a)

        # Report B (2026-07-11): 3 contractors (1 same but different workers, 1 same, 1 new)
        r_b = Report(
            date="2026-07-11",
            day="Saturday",
            status=ReportStatus.DRAFT,
            telegram_user="u1",
        )
        r_b.add_item(ReportItem(contractor="Alpha Builders", type="Civil", zone="Zone A", workers=15, details="More masonry"))
        r_b.add_item(ReportItem(contractor="Beta Electric", type="Electrical", zone="Zone B", workers=5))
        r_b.add_item(ReportItem(contractor="Gamma Roads", type="Civil", zone="Zone D", workers=8))
        report_repo.add(r_b)

    @pytest.fixture
    def seeded_identical_reports(self, report_repo: ReportRepository):
        """Seed two reports with identical contractors."""
        r_a = Report(
            date="2026-07-10",
            day="Friday",
            status=ReportStatus.FINAL,
            telegram_user="u1",
        )
        r_a.add_item(ReportItem(contractor="Alpha Builders", workers=10))
        r_a.add_item(ReportItem(contractor="Beta Electric", workers=5))
        report_repo.add(r_a)

        r_b = Report(
            date="2026-07-11",
            day="Saturday",
            status=ReportStatus.FINAL,
            telegram_user="u1",
        )
        r_b.add_item(ReportItem(contractor="Alpha Builders", workers=10))
        r_b.add_item(ReportItem(contractor="Beta Electric", workers=5))
        report_repo.add(r_b)

    @pytest.fixture
    def seeded_one_report(self, report_repo: ReportRepository):
        """Seed only one report — the other date has none."""
        r = Report(
            date="2026-07-10",
            day="Friday",
            status=ReportStatus.FINAL,
            telegram_user="u1",
        )
        r.add_item(ReportItem(contractor="Alpha Builders", workers=10))
        report_repo.add(r)

    # â”€â”€ Tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_compare_shows_added_removed_changed(
        self, service: DailyComparisonService, seeded_two_reports
    ):
        """Should correctly identify added, removed, and changed contractors."""
        result = service.compare("2026-07-10", "2026-07-11")

        assert result.date_a == "2026-07-10"
        assert result.date_b == "2026-07-11"
        assert result.contractor_count_a == 3
        assert result.contractor_count_b == 3
        assert result.worker_count_a == 18  # 10 + 5 + 3
        assert result.worker_count_b == 28  # 15 + 5 + 8
        assert result.has_differences is True

        # Added: Gamma Roads (8 workers)
        assert len(result.added_contractors) == 1
        assert result.added_contractors[0][0] == "Gamma Roads"
        assert result.added_contractors[0][1] == 8

        # Removed: Delta Pipes (3 workers)
        assert len(result.removed_contractors) == 1
        assert result.removed_contractors[0][0] == "Delta Pipes"
        assert result.removed_contractors[0][1] == 3

        # Changed: Alpha Builders (10 -> 15)
        assert len(result.changed_workers) == 1
        assert result.changed_workers[0][0] == "Alpha Builders"
        assert result.changed_workers[0][1] == 10
        assert result.changed_workers[0][2] == 15

        # Unchanged: Beta Electric (5 -> 5) should not appear in changed
        changed_names = [c[0] for c in result.changed_workers]
        assert "Beta Electric" not in changed_names

    def test_compare_identical_reports_no_differences(
        self, service: DailyComparisonService, seeded_identical_reports
    ):
        """Should report no differences for identical reports."""
        result = service.compare("2026-07-10", "2026-07-11")
        assert result.has_differences is False
        assert result.added_contractors == []
        assert result.removed_contractors == []
        assert result.changed_workers == []
        assert result.contractor_count_diff == 0
        assert result.worker_count_diff == 0

    def test_compare_same_report_to_itself(
        self, service: DailyComparisonService, seeded_two_reports
    ):
        """Should report no differences when comparing a report to itself."""
        result = service.compare("2026-07-10", "2026-07-10")
        assert result.has_differences is False
        assert result.added_contractors == []
        assert result.removed_contractors == []
        assert result.changed_workers == []

    def test_compare_missing_report_a_raises_error(
        self, service: DailyComparisonService, seeded_one_report
    ):
        """Should raise DailyComparisonError when report A is missing."""
        with pytest.raises(DailyComparisonError) as excinfo:
            service.compare("2099-01-01", "2026-07-10")
        assert "No report exists for 2099-01-01" in str(excinfo.value)

    def test_compare_missing_report_b_raises_error(
        self, service: DailyComparisonService, seeded_one_report
    ):
        """Should raise DailyComparisonError when report B is missing."""
        with pytest.raises(DailyComparisonError) as excinfo:
            service.compare("2026-07-10", "2099-01-01")
        assert "No report exists for 2099-01-01" in str(excinfo.value)

    def test_compare_both_missing_raises_error(
        self, service: DailyComparisonService, seeded_one_report
    ):
        """Should raise DailyComparisonError when both reports are missing."""
        with pytest.raises(DailyComparisonError) as excinfo:
            service.compare("2099-01-01", "2099-01-02")
        assert "2099-01-01" in str(excinfo.value)

    def test_compare_reports_direct(
        self, service: DailyComparisonService, seeded_two_reports
    ):
        """Should compare two Report objects directly via compare_reports()."""
        repo = service._repo
        r_a = repo.get_by_date("2026-07-10")
        r_b = repo.get_by_date("2026-07-11")
        assert r_a is not None
        assert r_b is not None

        result = service.compare_reports(r_a, r_b)
        assert result.has_differences is True
        assert result.contractor_count_a == 3
        assert result.contractor_count_b == 3

    def test_compare_empty_reports(
        self, service: DailyComparisonService, report_repo: ReportRepository
    ):
        """Should handle reports with no items (empty contractors list)."""
        r_a = Report(date="2026-07-10", day="Friday", status=ReportStatus.NO_REPORT, telegram_user="u1")
        r_b = Report(date="2026-07-11", day="Saturday", status=ReportStatus.NO_REPORT, telegram_user="u1")
        report_repo.add(r_a)
        report_repo.add(r_b)

        result = service.compare("2026-07-10", "2026-07-11")
        assert result.has_differences is False
        assert result.contractor_count_a == 0
        assert result.contractor_count_b == 0
        assert result.worker_count_a == 0
        assert result.worker_count_b == 0

    def test_compare_case_insensitive_matching(
        self, service: DailyComparisonService, report_repo: ReportRepository
    ):
        """Should match contractors case-insensitively."""
        r_a = Report(date="2026-07-10", day="Friday", status=ReportStatus.FINAL, telegram_user="u1")
        r_a.add_item(ReportItem(contractor="Alpha Builders", workers=10))
        report_repo.add(r_a)

        r_b = Report(date="2026-07-11", day="Saturday", status=ReportStatus.FINAL, telegram_user="u1")
        r_b.add_item(ReportItem(contractor="ALPHA BUILDERS", workers=15))
        report_repo.add(r_b)

        result = service.compare("2026-07-10", "2026-07-11")
        # Should match case-insensitively as a change, not add+remove
        assert len(result.added_contractors) == 0
        assert len(result.removed_contractors) == 0
        assert len(result.changed_workers) == 1
        assert result.changed_workers[0][0].lower() == "alpha builders"
        assert result.changed_workers[0][1] == 10
        assert result.changed_workers[0][2] == 15

    def test_compare_unchanged_contractors_not_in_changed(
        self, service: DailyComparisonService, report_repo: ReportRepository
    ):
        """Should not list contractors with same worker count as 'changed'."""
        r_a = Report(date="2026-07-10", day="Friday", status=ReportStatus.FINAL, telegram_user="u1")
        r_a.add_item(ReportItem(contractor="Alpha Builders", workers=10))
        r_a.add_item(ReportItem(contractor="Beta Electric", workers=5))
        report_repo.add(r_a)

        r_b = Report(date="2026-07-11", day="Saturday", status=ReportStatus.FINAL, telegram_user="u1")
        r_b.add_item(ReportItem(contractor="Alpha Builders", workers=10))
        r_b.add_item(ReportItem(contractor="Beta Electric", workers=5))
        report_repo.add(r_b)

        result = service.compare("2026-07-10", "2026-07-11")
        assert result.changed_workers == []
        assert result.has_differences is False

    def test_compare_worker_count_calculation(
        self, service: DailyComparisonService, report_repo: ReportRepository
    ):
        """Should correctly calculate worker counts for both reports."""
        r_a = Report(date="2026-07-10", day="Friday", status=ReportStatus.FINAL, telegram_user="u1")
        r_a.add_item(ReportItem(contractor="Alpha", workers=10))
        r_a.add_item(ReportItem(contractor="Beta", workers=None))  # None workers
        r_a.add_item(ReportItem(contractor="Gamma", workers=0))     # Zero workers
        report_repo.add(r_a)

        r_b = Report(date="2026-07-11", day="Saturday", status=ReportStatus.DRAFT, telegram_user="u1")
        r_b.add_item(ReportItem(contractor="Alpha", workers=15))
        r_b.add_item(ReportItem(contractor="Delta", workers=5))
        report_repo.add(r_b)

        result = service.compare("2026-07-10", "2026-07-11")
        # Report A: 10 + 0 (None→0) + 0 = 10
        assert result.worker_count_a == 10
        # Report B: 15 + 5 = 20
        assert result.worker_count_b == 20

    def test_compare_reports_with_mixed_status(
        self, service: DailyComparisonService, seeded_two_reports
    ):
        """Should capture report status in comparison result."""
        result = service.compare("2026-07-10", "2026-07-11")
        assert result.status_a == "final"
        assert result.status_b == "draft"

    def test_compare_with_no_contractors_in_one_report(
        self, service: DailyComparisonService, report_repo: ReportRepository
    ):
        """Should handle report B having contractors but report A having none."""
        r_a = Report(date="2026-07-10", day="Friday", status=ReportStatus.NO_REPORT, telegram_user="u1")
        report_repo.add(r_a)

        r_b = Report(date="2026-07-11", day="Saturday", status=ReportStatus.DRAFT, telegram_user="u1")
        r_b.add_item(ReportItem(contractor="Alpha Builders", workers=10))
        report_repo.add(r_b)

        result = service.compare("2026-07-10", "2026-07-11")
        assert result.contractor_count_a == 0
        assert result.contractor_count_b == 1
        assert len(result.added_contractors) == 1
        assert len(result.removed_contractors) == 0
        assert result.has_differences is True
