"""
Tests for ReportWorkflowService.

Covers:
- Valid transitions: Draft -> Final -> Locked -> Draft (admin)
- Invalid transitions (ReportLifecycleError raised)
- Admin-only unlock enforcement
- Auto-lock of expired reports
- EventLogService integration (events logged on each transition)
- Edge cases: None status, missing timestamps, already-locked reports
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from app.config.loader import ConfigLoader
from app.database.manager import DatabaseManager
from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.report_repository import ReportRepository
from app.services.event_log_service import EventLogService
from app.services.report_workflow_service import ReportWorkflowService
from app.utils.exceptions import ReportLifecycleError


class TestReportWorkflowService:
    """Test suite for ReportWorkflowService."""

    @pytest.fixture
    def db_path(self):
        """Create a temporary database file path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_workflow.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        """Create a DatabaseManager instance."""
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def event_log_repo(self, db_manager: DatabaseManager):
        """Create an EventLogRepository for direct queries in tests."""
        return EventLogRepository(db_manager)

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager, event_log_repo: EventLogRepository):
        """Create a ReportRepository with event logging."""
        return ReportRepository(db_manager, event_log_repo=event_log_repo)

    @pytest.fixture
    def event_log_service(self, db_manager: DatabaseManager):
        """Create an EventLogService (wraps DatabaseManager internally)."""
        return EventLogService(db_manager)

    @pytest.fixture
    def config(self):
        """Create an AppConfig with lifecycle settings."""
        cfg_dict = {
            "template": {"file": "templates/test.xlsx", "tables_file": "database/tables.xlsx"},
            "date": {"cell": "B4", "day_cell": "D4"},
            "table": {"start_row": 12, "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"}},
            "output": {"pdf_folder": "exports/pdf", "docs_folder": "exports/excel"},
            "database": {"path": "database/test.db"},
            "history": {"file": "database/history.xlsx"},
            "logging": {"file": "logs/app.log", "level": "INFO", "max_bytes": 10485760, "backup_count": 5},
            "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
            "lifecycle": {"auto_lock_hours": 24, "max_versions": 50},
        }
        return AppConfig(**cfg_dict)

    @pytest.fixture
    def workflow(self, repo: ReportRepository, event_log_service: EventLogService, config: AppConfig):
        """Create a ReportWorkflowService instance."""
        return ReportWorkflowService(repo, event_log_service, config)

    @pytest.fixture
    def draft_report(self, repo: ReportRepository) -> Report:
        """Create a draft report in the database."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            status=ReportStatus.DRAFT,
            telegram_user="user123",
            created_at="2026-07-11T08:00:00",
        )
        report.add_item(ReportItem(contractor="Civil", type="Civil Type", zone="Zone A", workers=10, details="5 Mason, 5 Helper"))
        return repo.add(report)

    @pytest.fixture
    def final_report(self, workflow: ReportWorkflowService, draft_report: Report) -> Report:
        """Create a finalized report."""
        return workflow.finalize_report(draft_report, telegram_user="user123")

    @pytest.fixture
    def locked_report(self, workflow: ReportWorkflowService, final_report: Report) -> Report:
        """Create a locked report."""
        return workflow.lock_report(final_report, telegram_user="user123")

    # ------------------------------------------------------------------
    # Valid Transitions
    # ------------------------------------------------------------------

    def test_finalize_draft_report(self, workflow: ReportWorkflowService, draft_report: Report):
        """Should transition Draft -> Final and set finalized_at."""
        result = workflow.finalize_report(draft_report, telegram_user="user123")
        assert result.status == ReportStatus.FINAL
        assert result.finalized_at is not None
        assert result.updated_at is not None
        assert result.updated_at >= result.finalized_at

    def test_lock_final_report(self, workflow: ReportWorkflowService, final_report: Report):
        """Should transition Final -> Locked and set locked_at/locked_by."""
        result = workflow.lock_report(final_report, telegram_user="user456")
        assert result.status == ReportStatus.LOCKED
        assert result.locked_at is not None
        assert result.locked_by == "user456"

    def test_unlock_locked_report(self, workflow: ReportWorkflowService, locked_report: Report):
        """Should transition Locked -> Draft when admin=True."""
        result = workflow.unlock_report(locked_report, telegram_user="admin", admin=True)
        assert result.status == ReportStatus.DRAFT

    def test_full_lifecycle(self, workflow: ReportWorkflowService, draft_report: Report):
        """Should complete the full cycle: Draft -> Final -> Locked -> Draft."""
        # Draft -> Final
        r1 = workflow.finalize_report(draft_report, telegram_user="user1")
        assert r1.status == ReportStatus.FINAL

        # Final -> Locked
        r2 = workflow.lock_report(r1, telegram_user="user2")
        assert r2.status == ReportStatus.LOCKED

        # Locked -> Draft (admin)
        r3 = workflow.unlock_report(r2, telegram_user="admin", admin=True)
        assert r3.status == ReportStatus.DRAFT

    # ------------------------------------------------------------------
    # Invalid Transitions
    # ------------------------------------------------------------------

    def test_finalize_already_final(self, workflow: ReportWorkflowService, final_report: Report):
        """Should raise when trying to finalize an already-final report."""
        with pytest.raises(ReportLifecycleError) as exc:
            workflow.finalize_report(final_report, telegram_user="user")
        assert "final" in str(exc.value).lower()

    def test_finalize_locked(self, workflow: ReportWorkflowService, locked_report: Report):
        """Should raise when trying to finalize a locked report."""
        with pytest.raises(ReportLifecycleError):
            workflow.finalize_report(locked_report, telegram_user="user")

    def test_lock_draft_report(self, workflow: ReportWorkflowService, draft_report: Report):
        """Should raise when trying to lock a draft report (must finalize first)."""
        with pytest.raises(ReportLifecycleError):
            workflow.lock_report(draft_report, telegram_user="user")

    def test_lock_locked_report(self, workflow: ReportWorkflowService, locked_report: Report):
        """Should raise when trying to lock an already-locked report."""
        with pytest.raises(ReportLifecycleError):
            workflow.lock_report(locked_report, telegram_user="user")

    def test_unlock_draft_report(self, workflow: ReportWorkflowService, draft_report: Report):
        """Should raise when trying to unlock a draft report."""
        with pytest.raises(ReportLifecycleError):
            workflow.unlock_report(draft_report, telegram_user="admin", admin=True)

    def test_unlock_final_report(self, workflow: ReportWorkflowService, final_report: Report):
        """Should raise when trying to unlock a final report."""
        with pytest.raises(ReportLifecycleError):
            workflow.unlock_report(final_report, telegram_user="admin", admin=True)

    def test_unlock_non_admin(self, workflow: ReportWorkflowService, locked_report: Report):
        """Should raise when non-admin tries to unlock."""
        with pytest.raises(ReportLifecycleError) as exc:
            workflow.unlock_report(locked_report, telegram_user="regular_user")
        assert "admin" in str(exc.value).lower()

    # ------------------------------------------------------------------
    # Admin-only unlock
    # ------------------------------------------------------------------

    def test_non_admin_unlock_rejected(self, workflow: ReportWorkflowService, locked_report: Report):
        """Should reject non-admin unlock attempts."""
        with pytest.raises(ReportLifecycleError):
            workflow.unlock_report(locked_report, telegram_user="user123")

    def test_admin_unlock_allowed(self, workflow: ReportWorkflowService, locked_report: Report):
        """Should allow admin unlock."""
        result = workflow.unlock_report(locked_report, telegram_user="admin", admin=True)
        assert result.status == ReportStatus.DRAFT

    # ------------------------------------------------------------------
    # Auto-lock
    # ------------------------------------------------------------------

    def test_auto_lock_disabled(self, repo: ReportRepository, event_log_service: EventLogService):
        """Should skip auto-lock when auto_lock_hours=0."""
        cfg = AppConfig(**{
            "template": {"file": "templates/test.xlsx", "tables_file": "database/tables.xlsx"},
            "date": {"cell": "B4", "day_cell": "D4"},
            "table": {"start_row": 12, "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"}},
            "output": {"pdf_folder": "exports/pdf", "docs_folder": "exports/excel"},
            "database": {"path": "database/test.db"},
            "history": {"file": "database/history.xlsx"},
            "logging": {"file": "logs/app.log", "level": "INFO", "max_bytes": 10485760, "backup_count": 5},
            "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
            "lifecycle": {"auto_lock_hours": 0, "max_versions": 50},
        })
        wf = ReportWorkflowService(repo, event_log_service, cfg)
        count = wf.auto_lock_reports()
        assert count == 0

    def test_auto_lock_expired_report(
        self, repo: ReportRepository, event_log_service: EventLogService, config: AppConfig
    ):
        """Should auto-lock a report past the auto-lock threshold."""
        # Create a finalized report from long ago
        old_time = (datetime.now() - timedelta(hours=48)).isoformat()
        report = Report(
            date="2026-07-09",
            day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL,
            telegram_user="user1",
            created_at=old_time,
            finalized_at=old_time,
        )
        repo.add(report)

        # Auto-lock with 24-hour threshold
        count = ReportWorkflowService(repo, event_log_service, config).auto_lock_reports()
        assert count == 1

        # Verify it's now locked
        locked = repo.get_by_id(report.id)
        assert locked is not None
        assert locked.status == ReportStatus.LOCKED

    def test_auto_lock_recent_report(
        self, repo: ReportRepository, event_log_service: EventLogService, config: AppConfig
    ):
        """Should NOT auto-lock a recently finalized report."""
        recent_time = datetime.now().isoformat()
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            status=ReportStatus.FINAL,
            telegram_user="user1",
            created_at=recent_time,
            finalized_at=recent_time,
        )
        repo.add(report)

        count = ReportWorkflowService(repo, event_log_service, config).auto_lock_reports()
        assert count == 0

    def test_auto_lock_skips_draft(
        self, repo: ReportRepository, event_log_service: EventLogService, config: AppConfig
    ):
        """Should skip draft reports during auto-lock."""
        old_time = (datetime.now() - timedelta(hours=48)).isoformat()
        report = Report(
            date="2026-07-09",
            day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.DRAFT,
            telegram_user="user1",
            created_at=old_time,
        )
        repo.add(report)

        count = ReportWorkflowService(repo, event_log_service, config).auto_lock_reports()
        assert count == 0

    # ------------------------------------------------------------------
    # Event Logging Integration
    # ------------------------------------------------------------------

    def test_finalize_logs_event(
        self, workflow: ReportWorkflowService, draft_report: Report, event_log_repo: EventLogRepository
    ):
        """Should log report.finalized event on finalize."""
        workflow.finalize_report(draft_report, telegram_user="user123")
        events = event_log_repo.get_by_object("report", draft_report.id)
        assert any(e.action == "report.finalized" for e in events)

    def test_lock_logs_event(
        self, workflow: ReportWorkflowService, final_report: Report, event_log_repo: EventLogRepository
    ):
        """Should log report.locked event on lock."""
        workflow.lock_report(final_report, telegram_user="user456")
        events = event_log_repo.get_by_object("report", final_report.id)
        assert any(e.action == "report.locked" for e in events)

    def test_unlock_logs_event(
        self, workflow: ReportWorkflowService, locked_report: Report, event_log_repo: EventLogRepository
    ):
        """Should log report.unlocked event on unlock."""
        workflow.unlock_report(locked_report, telegram_user="admin", admin=True)
        events = event_log_repo.get_by_object("report", locked_report.id)
        assert any(e.action == "report.unlocked" for e in events)

    def test_auto_lock_logs_events(
        self, repo: ReportRepository, event_log_service: EventLogService,
        event_log_repo: EventLogRepository, config: AppConfig
    ):
        """Should log report.locked event for auto-locked reports."""
        old_time = (datetime.now() - timedelta(hours=48)).isoformat()
        report = Report(
            date="2026-07-09",
            day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL,
            telegram_user="user1",
            created_at=old_time,
            finalized_at=old_time,
        )
        inserted = repo.add(report)

        wf = ReportWorkflowService(repo, event_log_service, config)
        wf.auto_lock_reports()

        events = event_log_repo.get_by_object("report", inserted.id)
        assert any(e.action == "report.locked" for e in events)

    # ------------------------------------------------------------------
    # Auto-finalize (mventor-ticket-040)
    # ------------------------------------------------------------------

    def test_auto_finalize_disabled_when_hour_negative(
        self, repo: ReportRepository, event_log_service: EventLogService
    ):
        """Should skip auto-finalize when auto_finalize_hour < 0."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": -1}}))
        wf = ReportWorkflowService(repo, event_log_service, cfg)
        count = wf.auto_finalize_drafts(current_hour=17, current_minute=0)
        assert count == 0

    def test_auto_finalize_disabled_before_deadline(
        self, repo: ReportRepository, event_log_service: EventLogService
    ):
        """Should NOT finalize drafts before the configured deadline."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": 17}}))
        report = self._create_draft_report(repo, date="2026-07-14")
        wf = ReportWorkflowService(repo, event_log_service, cfg)

        # Before deadline (16:59)
        count = wf.auto_finalize_drafts(current_hour=16, current_minute=59)
        assert count == 0
        saved = repo.get_by_id(report.id)
        assert saved is not None
        assert saved.status == ReportStatus.DRAFT

    def test_auto_finalize_at_deadline(
        self, repo: ReportRepository, event_log_service: EventLogService
    ):
        """Should finalize today's draft report at the deadline."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": 17}}))
        report = self._create_draft_report(repo, date="2026-07-14")
        wf = ReportWorkflowService(repo, event_log_service, cfg)

        # At deadline (17:00)
        count = wf.auto_finalize_drafts(current_hour=17, current_minute=0)
        assert count == 1
        saved = repo.get_by_id(report.id)
        assert saved is not None
        assert saved.status == ReportStatus.FINAL
        assert saved.finalized_at is not None

    def test_auto_finalize_after_deadline(
        self, repo: ReportRepository, event_log_service: EventLogService
    ):
        """Should finalize today's draft report after the deadline."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": 17}}))
        report = self._create_draft_report(repo, date="2026-07-14")
        wf = ReportWorkflowService(repo, event_log_service, cfg)

        # After deadline (17:30)
        count = wf.auto_finalize_drafts(current_hour=17, current_minute=30)
        assert count == 1
        saved = repo.get_by_id(report.id)
        assert saved is not None
        assert saved.status == ReportStatus.FINAL

    def test_auto_finalize_skips_final_reports(
        self, workflow: ReportWorkflowService, final_report: Report
    ):
        """Should skip already-finalized reports."""
        count = workflow.auto_finalize_drafts(current_hour=17, current_minute=0)
        assert count == 0

    def test_auto_finalize_skips_locked_reports(
        self, workflow: ReportWorkflowService, locked_report: Report
    ):
        """Should skip locked reports."""
        count = workflow.auto_finalize_drafts(current_hour=17, current_minute=0)
        assert count == 0

    def test_auto_finalize_skips_empty_draft(
        self, repo: ReportRepository, event_log_service: EventLogService
    ):
        """Should skip draft reports with no items."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": 17}}))
        report = Report(
            date="2026-07-14",
            day="Ø§Ù„Ø«Ù„Ø§Ø«Ø§Ø¡",
            status=ReportStatus.DRAFT,
            telegram_user="user1",
        )
        repo.add(report)
        wf = ReportWorkflowService(repo, event_log_service, cfg)

        count = wf.auto_finalize_drafts(current_hour=17, current_minute=0)
        assert count == 0

    def test_auto_finalize_skips_other_dates(
        self, repo: ReportRepository, event_log_service: EventLogService
    ):
        """Should skip draft reports from other dates."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": 17}}))
        # Older report
        old = self._create_draft_report(repo, date="2026-07-10")
        wf = ReportWorkflowService(repo, event_log_service, cfg)

        count = wf.auto_finalize_drafts(current_hour=17, current_minute=0)
        assert count == 0  # Not today's report

    def test_auto_finalize_logs_events(
        self, repo: ReportRepository, event_log_service: EventLogService,
        event_log_repo: EventLogRepository
    ):
        """Should log report.finalized event for auto-finalized reports."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": 17}}))
        report = self._create_draft_report(repo, date="2026-07-14")
        wf = ReportWorkflowService(repo, event_log_service, cfg)

        wf.auto_finalize_drafts(current_hour=17, current_minute=0)

        events = event_log_repo.get_by_object("report", report.id)
        assert any(e.action == "report.finalized" for e in events)

    def test_auto_finalize_with_custom_deadline(
        self, repo: ReportRepository, event_log_service: EventLogService
    ):
        """Should use custom deadline from config (e.g. 15:30)."""
        cfg = AppConfig(**self._make_cfg({"lifecycle": {"auto_lock_hours": 0, "max_versions": 50, "auto_finalize_hour": 15, "auto_finalize_minute": 30}}))
        report = self._create_draft_report(repo, date="2026-07-14")
        wf = ReportWorkflowService(repo, event_log_service, cfg)

        # Before custom deadline (15:29) â€” should NOT finalize
        count = wf.auto_finalize_drafts(current_hour=15, current_minute=29)
        assert count == 0

        # At custom deadline (15:30) â€” should finalize
        count = wf.auto_finalize_drafts(current_hour=15, current_minute=30)
        assert count == 1
        saved = repo.get_by_id(report.id)
        assert saved is not None
        assert saved.status == ReportStatus.FINAL

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_cfg(overrides: dict) -> dict:
        """Create a base config dict with optional overrides for testing."""
        base = {
            "template": {"file": "templates/test.xlsx", "tables_file": "database/tables.xlsx"},
            "date": {"cell": "B4", "day_cell": "D4"},
            "table": {"start_row": 12, "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"}},
            "output": {"pdf_folder": "exports/pdf", "docs_folder": "exports/excel"},
            "database": {"path": "database/test.db"},
            "history": {"file": "database/history.xlsx"},
            "logging": {"file": "logs/app.log", "level": "INFO", "max_bytes": 10485760, "backup_count": 5},
            "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
            "lifecycle": {"auto_lock_hours": 24, "max_versions": 50},
        }
        result = base.copy()
        # Deep-merge overrides
        for key, val in overrides.items():
            if isinstance(val, dict) and key in result and isinstance(result[key], dict):
                result[key].update(val)
            else:
                result[key] = val
        return result

    @staticmethod
    def _create_draft_report(repo: ReportRepository, date: str = "2026-07-14") -> Report:
        """Helper: create a draft report with one item for the given date."""
        from datetime import date as dt_date
        import calendar
        from app.services.arabic_date_service import ArabicDateService

        d = dt_date.fromisoformat(date)
        day_name = ArabicDateService.get_arabic_day_name(d)
        report = Report(
            date=date,
            day=day_name,
            status=ReportStatus.DRAFT,
            telegram_user="user1",
        )
        report.add_item(ReportItem(contractor="Al Faris", type="Civil", zone="Zone A", workers=10, details="5+5"))
        return repo.add(report)

    # ------------------------------------------------------------------
    # Edge Cases
    # ------------------------------------------------------------------

    def test_report_with_no_status(self, workflow: ReportWorkflowService, draft_report: Report):
        """Should raise ReportLifecycleError for a report with no status."""
        draft_report.status = None  # type: ignore
        with pytest.raises(ReportLifecycleError):
            workflow.finalize_report(draft_report, telegram_user="user")

    def test_user_message_in_error(self, workflow: ReportWorkflowService, draft_report: Report):
        """Should provide a helpful user_message in ReportLifecycleError."""
        with pytest.raises(ReportLifecycleError) as exc:
            workflow.lock_report(draft_report, telegram_user="user")
        msg = exc.value.user_message
        assert "draft" in msg.lower() or "final" in msg.lower()

    def test_admin_unlock_user_message(self, workflow: ReportWorkflowService, locked_report: Report):
        """Should inform non-admin users about admin requirement."""
        with pytest.raises(ReportLifecycleError) as exc:
            workflow.unlock_report(locked_report, telegram_user="regular")
        assert "admin" in str(exc.value.user_message).lower()

    def test_transition_error_contains_allowed(self, workflow: ReportWorkflowService, draft_report: Report):
        """Should list allowed transitions in error message."""
        with pytest.raises(ReportLifecycleError) as exc:
            workflow.lock_report(draft_report, telegram_user="user")
        assert "draft" in str(exc.value).lower()
        # The allowed transitions from draft should be mentioned
        assert "final" in str(exc.value).lower()

    # ------------------------------------------------------------------
    # Version History Integration (mventor-ticket-024)
    # ------------------------------------------------------------------

    @pytest.fixture
    def version_repo(self, db_manager: DatabaseManager, repo: ReportRepository):
        """Create a VersionRepository for testing."""
        from app.repositories.version_repository import VersionRepository
        return VersionRepository(db_manager, repo)

    @pytest.fixture
    def workflow_with_versions(
        self, repo: ReportRepository, event_log_service: EventLogService,
        config: AppConfig, version_repo
    ):
        """Create a ReportWorkflowService with version tracking enabled."""
        return ReportWorkflowService(repo, event_log_service, config,
                                      version_repository=version_repo)

    def test_version_created_on_finalize(
        self, workflow_with_versions: ReportWorkflowService,
        draft_report: Report, version_repo
    ):
        """Should create a version snapshot when finalizing a report."""
        finalized = workflow_with_versions.finalize_report(draft_report, telegram_user="user123")
        versions = version_repo.get_versions(finalized.id)
        assert len(versions) == 1, (
            f"Expected 1 version, got {len(versions)}"
        )
        assert versions[0].version_number == 1
        assert versions[0].created_by == "user123"
        assert versions[0].change_summary == "Report finalized"

    def test_version_contains_report_data(
        self, workflow_with_versions: ReportWorkflowService,
        draft_report: Report, version_repo
    ):
        """Version snapshot should contain the full report state."""
        finalized = workflow_with_versions.finalize_report(draft_report, telegram_user="user")
        versions = version_repo.get_versions(finalized.id)
        assert len(versions) == 1
        snapshot = versions[0].snapshot
        assert "Civil" in snapshot, "Snapshot should contain contractor name"
        assert "Zone A" in snapshot, "Snapshot should contain zone"
        assert "10" in snapshot, "Snapshot should contain worker count"

    def test_sequential_versions_on_multiple_finalizes(
        self, workflow_with_versions: ReportWorkflowService,
        repo: ReportRepository, version_repo
    ):
        """Multiple finalizes should create versions 1, 2, 3..."""
        # Create draft
        draft = Report(
            date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        draft.add_item(ReportItem(contractor="Civil", workers=5))
        draft = repo.add(draft)

        # Finalize once -> version 1
        r1 = workflow_with_versions.finalize_report(draft, telegram_user="u1")
        assert len(version_repo.get_versions(r1.id)) == 1

        # Full cycle back to Draft: Final -> Locked -> Draft (admin unlock)
        locked = workflow_with_versions.lock_report(r1, telegram_user="admin")
        unlocked = workflow_with_versions.unlock_report(locked, telegram_user="admin", admin=True)
        assert unlocked.status == ReportStatus.DRAFT

        # Re-finalize -> version 2
        r2 = workflow_with_versions.finalize_report(unlocked, telegram_user="u2")

        versions = version_repo.get_versions(r2.id)
        assert len(versions) == 2, (
            f"Expected 2 versions, got {len(versions)}"
        )
        assert versions[0].version_number == 1
        assert versions[1].version_number == 2
        assert versions[1].created_by == "u2"

    def test_no_version_without_version_repo(
        self, workflow: ReportWorkflowService, draft_report: Report, db_manager
    ):
        """Should NOT create versions when version_repo is not provided (backward compat)."""
        finalized = workflow.finalize_report(draft_report, telegram_user="user123")
        # Verify no versions exist
        row = db_manager.execute(
            "SELECT COUNT(*) as cnt FROM report_versions WHERE report_id = ?",
            (finalized.id,),
        ).fetchone()
        assert row["cnt"] == 0, "No versions should exist without version_repo"

    def test_version_failure_does_not_block_finalize(
        self, workflow: ReportWorkflowService, draft_report: Report,
        repo: ReportRepository, event_log_service: EventLogService, config: AppConfig
    ):
        """Finalize should succeed even if version creation fails."""
        # Use a broken version_repo that raises on create_version
        class BrokenVersionRepo:
            def create_version(self, **kwargs):
                raise RuntimeError("Version creation failed!")
            def prune_versions(self, report_id, max_ver):
                return 0

        broken_wf = ReportWorkflowService(
            repo, event_log_service, config,
            version_repository=BrokenVersionRepo(),  # type: ignore
        )
        # Should not raise â€” version failure is caught and logged
        result = broken_wf.finalize_report(draft_report, telegram_user="user")
        assert result.status == ReportStatus.FINAL
        assert result.finalized_at is not None

    def test_pruning_after_finalize(
        self, repo: ReportRepository, event_log_service: EventLogService,
        db_manager, draft_report: Report
    ):
        """Should prune old versions when max_versions is exceeded."""
        from app.repositories.version_repository import VersionRepository

        # Use a config with max_versions=2
        cfg = AppConfig(**{
            "template": {"file": "t.xlsx", "tables_file": "database/tables.xlsx"},
            "date": {"cell": "B4", "day_cell": "D4"},
            "table": {"start_row": 12, "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"}},
            "output": {"pdf_folder": "exports/pdf", "docs_folder": "exports/excel"},
            "database": {"path": "database/test.db"},
            "history": {"file": "database/history.xlsx"},
            "logging": {"file": "logs/app.log", "level": "INFO", "max_bytes": 10485760, "backup_count": 5},
            "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
            "lifecycle": {"auto_lock_hours": 0, "max_versions": 2},
        })

        version_repo = VersionRepository(db_manager, repo)
        wf = ReportWorkflowService(repo, event_log_service, cfg,
                                    version_repository=version_repo)

        # Finalize first time -> version 1
        finalized = wf.finalize_report(draft_report, telegram_user="u1")
        assert len(version_repo.get_versions(finalized.id)) == 1

        # Cycle through Lock -> Unlock -> Finalize 3 more times
        for i in range(3):
            locked = wf.lock_report(finalized, telegram_user="admin")
            unlocked = wf.unlock_report(locked, telegram_user="admin", admin=True)
            finalized = wf.finalize_report(unlocked, telegram_user=f"u{i+2}")

        # Should keep at most 2 versions
        versions = version_repo.get_versions(finalized.id)
        assert len(versions) == 2, (
            f"Expected 2 versions after pruning, got {len(versions)}"
        )
        # The kept versions should be the most recent ones
        assert versions[0].version_number >= 3
        assert versions[1].version_number == 4
