"""
Tests for DailyDashboardService. (mventor-ticket-014)

Covers:
- Arabic date and day name formatting
- Current time display
- Report status detection (not_created, draft, final, locked, no_report)
- Contractor count and total workers
- Time remaining calculation
- Quick action buttons for each state
- Config show_time_remaining toggle
"""

import tempfile
from datetime import datetime, date, time, timedelta
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.daily_dashboard_service import (
    DailyDashboardService,
    DashboardData,
)
from app.services.arabic_date_service import ArabicDateService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides) -> AppConfig:
    defaults = {
        "template": {"file": "t.xlsx", "tables_file": "database/tables.xlsx"},
        "date": {"cell": "B4", "day_cell": "D4"},
        "table": {"start_row": 12, "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"}},
        "output": {"pdf_folder": "exports", "docs_folder": "exports"},
        "database": {"path": "test.db"},
        "history": {"file": "h.xlsx"},
        "logging": {"file": "l.log", "level": "INFO", "max_bytes": 1024, "backup_count": 1},
        "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
        "dashboard": {"show_time_remaining": True, "deadline_hour": 14, "deadline_minute": 0},
    }
    if "dashboard" in overrides:
        defaults["dashboard"] = overrides.pop("dashboard")
    if overrides:
        defaults.update(overrides)
    return AppConfig(**defaults)


# ---------------------------------------------------------------------------
# DashboardData dataclass
# ---------------------------------------------------------------------------

class TestDashboardData:
    """Tests for the DashboardData dataclass."""

    def test_creation(self):
        data = DashboardData(
            date="١٥ / ٠٨ / ٢٠٢٦",
            day="السبت",
            time="10:30",
            report_status="draft",
            contractor_count=3,
            total_workers=25,
            time_remaining="3h 30m",
            buttons=["Open Draft", "Search"],
        )
        assert data.date == "١٥ / ٠٨ / ٢٠٢٦"
        assert data.day == "السبت"
        assert data.time == "10:30"
        assert data.report_status == "draft"
        assert data.contractor_count == 3
        assert data.total_workers == 25
        assert data.time_remaining == "3h 30m"
        assert data.buttons == ["Open Draft", "Search"]

    def test_default_buttons_empty(self):
        data = DashboardData(
            date="x", day="y", time="z",
            report_status="draft", contractor_count=0, total_workers=0,
            time_remaining="",
        )
        assert data.buttons == []


# ---------------------------------------------------------------------------
# DailyDashboardService
# ---------------------------------------------------------------------------

class TestDailyDashboardService:
    """Test suite for DailyDashboardService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_dashboard.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return ReportRepository(db_manager)

    @pytest.fixture
    def service(self, repo: ReportRepository):
        return DailyDashboardService(repo, make_config())

    # ------------------------------------------------------------------
    # Date / time formatting
    # ------------------------------------------------------------------

    def test_arabic_date_format(self, service: DailyDashboardService):
        """Dashboard should show Arabic-formatted date."""
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        expected_date = ArabicDateService.get_arabic_date(date(2026, 7, 11))
        assert dash.date == expected_date, (
            f"Expected {expected_date}, got {dash.date}"
        )

    def test_arabic_day_name(self, service: DailyDashboardService):
        """Dashboard should show Arabic day name."""
        fixed_now = datetime(2026, 7, 11, 10, 0)  # Saturday
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        expected_day = ArabicDateService.get_arabic_day_name(date(2026, 7, 11))
        assert dash.day == expected_day, (
            f"Expected {expected_day}, got {dash.day}"
        )

    def test_current_time(self, service: DailyDashboardService):
        """Dashboard should show current server time."""
        fixed_now = datetime(2026, 7, 11, 9, 5)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.time == "09:05", f"Expected 09:05, got {dash.time}"

    # ------------------------------------------------------------------
    # Report status
    # ------------------------------------------------------------------

    def test_status_not_created(self, service: DailyDashboardService):
        """Should show 'not_created' when no report exists."""
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.report_status == "not_created"

    def test_status_draft(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should show 'draft' when a draft report exists."""
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.DRAFT, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.report_status == "draft"

    def test_status_final(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should show 'final' when a finalized report exists."""
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.FINAL, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.report_status == "final"

    def test_status_locked(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should show 'locked' when a locked report exists."""
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.LOCKED, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.report_status == "locked"

    def test_status_no_report(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should show 'no_report' when no_report status is set."""
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.NO_REPORT, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.report_status == "no_report"

    # ------------------------------------------------------------------
    # Contractor count / total workers
    # ------------------------------------------------------------------

    def test_contractor_count(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should count contractors in today's report."""
        report = Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        report.add_item(ReportItem(contractor="C1", workers=5))
        report.add_item(ReportItem(contractor="C2", workers=3))
        report.add_item(ReportItem(contractor="C3", workers=7))
        repo.add(report)

        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.contractor_count == 3, (
            f"Expected 3 contractors, got {dash.contractor_count}"
        )

    def test_total_workers(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should sum all workers in today's report."""
        report = Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        report.add_item(ReportItem(contractor="C1", workers=10))
        report.add_item(ReportItem(contractor="C2", workers=20))
        report.add_item(ReportItem(contractor="C3", workers=30))
        repo.add(report)

        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.total_workers == 60, (
            f"Expected 60 workers, got {dash.total_workers}"
        )

    def test_no_report_zero_counts(self, service: DailyDashboardService):
        """Should show zero counts when no report exists."""
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.contractor_count == 0
        assert dash.total_workers == 0

    def test_no_report_status_zero_counts(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should show zero counts when no_report status."""
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.NO_REPORT, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.contractor_count == 0
        assert dash.total_workers == 0

    def test_workers_none_handled(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should handle items with workers=None without error."""
        report = Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        report.add_item(ReportItem(contractor="C1", workers=None))
        repo.add(report)

        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.contractor_count == 1
        assert dash.total_workers == 0

    # ------------------------------------------------------------------
    # Time remaining
    # ------------------------------------------------------------------

    def test_time_remaining_before_deadline(self, service: DailyDashboardService):
        """Should show remaining time before deadline."""
        # 10:30 AM, deadline at 14:00 -> 3h 30m
        fixed_now = datetime(2026, 7, 11, 10, 30)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.time_remaining == "3h 30m", (
            f"Expected '3h 30m', got '{dash.time_remaining}'"
        )

    def test_time_remaining_exactly_at_deadline(self, service: DailyDashboardService):
        """Should show 'Closed' at exactly the deadline."""
        fixed_now = datetime(2026, 7, 11, 14, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.time_remaining == "Closed"

    def test_time_remaining_after_deadline(self, service: DailyDashboardService):
        """Should show 'Closed' after deadline."""
        fixed_now = datetime(2026, 7, 11, 15, 30)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.time_remaining == "Closed"

    def test_time_remaining_one_minute_before(self, service: DailyDashboardService):
        """Should show 0h 01m one minute before deadline."""
        fixed_now = datetime(2026, 7, 11, 13, 59)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.time_remaining == "0h 01m"

    def test_time_remaining_disabled(self, repo: ReportRepository):
        """Should return empty string when show_time_remaining is False."""
        cfg = make_config(dashboard={"show_time_remaining": False})
        svc = DailyDashboardService(repo, cfg)
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = svc.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.time_remaining == ""

    def test_custom_deadline(self, repo: ReportRepository):
        """Should respect custom deadline config."""
        cfg = make_config(dashboard={"deadline_hour": 17, "deadline_minute": 30})
        svc = DailyDashboardService(repo, cfg)
        fixed_now = datetime(2026, 7, 11, 15, 0)
        dash = svc.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.time_remaining == "2h 30m", (
            f"Expected '2h 30m', got '{dash.time_remaining}'"
        )

    # ------------------------------------------------------------------
    # Quick action buttons
    # ------------------------------------------------------------------

    def test_buttons_not_created(self, service: DailyDashboardService):
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.buttons == ["Create Report", "Search"]

    def test_buttons_no_report(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.NO_REPORT, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.buttons == ["Create Report", "Search"]

    def test_buttons_draft(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.DRAFT, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.buttons == ["Open Draft", "Search"]

    def test_buttons_final(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.FINAL, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.buttons == ["View Report", "Search"]

    def test_buttons_locked(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        repo.add(Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.LOCKED, telegram_user="u1",
        ))
        fixed_now = datetime(2026, 7, 11, 10, 0)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)
        assert dash.buttons == ["View Report", "Search"]

    # ------------------------------------------------------------------
    # Integration: full dashboard
    # ------------------------------------------------------------------

    def test_full_dashboard_draft(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Full dashboard for a draft report should have all fields populated."""
        report = Report(
            date="2026-07-11", day="السبت",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        report.add_item(ReportItem(contractor="Civil Co", workers=10))
        report.add_item(ReportItem(contractor="Electric Inc", workers=5))
        repo.add(report)

        fixed_now = datetime(2026, 7, 11, 11, 30)
        dash = service.get_dashboard(today="2026-07-11", now_time=fixed_now)

        assert dash.date == ArabicDateService.get_arabic_date(date(2026, 7, 11))
        assert dash.day == "السبت"
        assert dash.time == "11:30"
        assert dash.report_status == "draft"
        assert dash.contractor_count == 2
        assert dash.total_workers == 15
        assert dash.time_remaining == "2h 30m"
        assert dash.buttons == ["Open Draft", "Search"]

    def test_default_today_used_when_not_specified(
        self, service: DailyDashboardService, repo: ReportRepository
    ):
        """Should default to system today when no date is given."""
        # Create a report for system today
        today_str = date.today().isoformat()
        repo.add(Report(
            date=today_str, day="test",
            status=ReportStatus.DRAFT, telegram_user="u1",
        ))
        now = datetime.now()
        dash = service.get_dashboard(now_time=now)
        assert dash.report_status == "draft"
