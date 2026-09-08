"""
Tests for AutoSaveService. (mventor-ticket-008)

Covers:
- save_draft: new report creation, existing report update, re-open finalized
- auto_save_item: add item to existing report, modify item, verify persistence
- remove_item_and_save: remove item and save
- Event logging integration
- Crash resilience: verify_session_consistency
- Edge cases: empty items list, no ID, duplicate items
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.report_repository import ReportRepository
from app.services.auto_save_service import AutoSaveService
from app.services.event_log_service import EventLogService
from app.utils.exceptions import ReportLifecycleError


class TestAutoSaveService:
    """Test suite for AutoSaveService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_autosave.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def event_log_repo(self, db_manager: DatabaseManager):
        return EventLogRepository(db_manager)

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager, event_log_repo: EventLogRepository):
        return ReportRepository(db_manager, event_log_repo=event_log_repo)

    @pytest.fixture
    def event_log_service(self, db_manager: DatabaseManager):
        return EventLogService(db_manager)

    @pytest.fixture
    def auto_save(self, repo: ReportRepository, event_log_service: EventLogService):
        return AutoSaveService(repo, event_log_service)

    # ------------------------------------------------------------------
    # save_draft â€” new report
    # ------------------------------------------------------------------

    def test_save_draft_creates_new_report(self, auto_save: AutoSaveService):
        """Should create a new draft report with DRAFT status."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        saved = auto_save.save_draft(report, telegram_user="user1")
        assert saved.id is not None
        assert saved.status == ReportStatus.DRAFT
        assert saved.updated_at is not None

    def test_save_draft_creates_with_items(self, auto_save: AutoSaveService):
        """Should persist items when creating a new draft."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        report.add_item(ReportItem(contractor="Civil", workers=10, zone="Zone A"))
        report.add_item(ReportItem(contractor="Electrical", workers=5, zone="Zone B"))
        saved = auto_save.save_draft(report, telegram_user="user1")
        assert len(saved.items) == 2
        for item in saved.items:
            assert item.id is not None

    def test_save_draft_updates_existing_by_id(self, auto_save: AutoSaveService):
        """Should update a report when it already has an ID."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        report.add_item(ReportItem(contractor="Civil", workers=10))
        saved = auto_save.save_draft(report, telegram_user="user1")

        # Modify and save again
        saved.add_item(ReportItem(contractor="Electrical", workers=5))
        re_saved = auto_save.save_draft(saved, telegram_user="user1")
        assert re_saved.id == saved.id
        assert len(re_saved.items) == 2

    def test_save_draft_finds_existing_by_date(self, auto_save: AutoSaveService):
        """Should find existing report by date when no ID is set."""
        # Create first
        report1 = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        report1.add_item(ReportItem(contractor="Civil", workers=10))
        saved1 = auto_save.save_draft(report1, telegram_user="user1")

        # Now create a new Report object with same date, no ID
        report2 = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        report2.add_item(ReportItem(contractor="Electrical", workers=5))
        saved2 = auto_save.save_draft(report2, telegram_user="user1")

        # Should have updated the existing report
        assert saved2.id == saved1.id
        assert len(saved2.items) == 1  # Replaced
        assert saved2.items[0].contractor == "Electrical"

    def test_save_draft_raises_on_finalized_report(self, auto_save: AutoSaveService):
        """Should raise ReportLifecycleError when trying to edit a finalized report
        (the handler should use ReportWorkflowService.unlock_report instead)."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        report.add_item(ReportItem(contractor="Civil", workers=10))
        saved = auto_save.save_draft(report, telegram_user="user1")

        # Manually set to FINAL in DB (simulating a finalized report)
        from app.repositories.report_repository import ReportRepository
        # Use the repo directly to update status without lifecycle check
        saved.status = ReportStatus.FINAL
        saved.finalized_at = "2026-07-11T12:00:00"
        # Need to use force=True to bypass lifecycle validation
        self._force_update_status(auto_save, saved)

        # Now save_draft should raise because report is finalized in DB
        saved2 = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="user1")
        with pytest.raises(ReportLifecycleError):
            auto_save.save_draft(saved2, telegram_user="user1")

    def _force_update_status(self, auto_save: AutoSaveService, report: Report):
        """Bypass lifecycle validation to set a report's status directly."""
        now = datetime.now().isoformat()
        auto_save._repo._db.execute(
            """UPDATE reports SET status=?, finalized_at=?, updated_at=? WHERE id=?""",
            (report.status.value, report.finalized_at, now, report.id),
        )
        auto_save._repo._db.commit()

    # ------------------------------------------------------------------
    # auto_save_item
    # ------------------------------------------------------------------

    def test_auto_save_item_adds_and_persists(self, auto_save: AutoSaveService):
        """Should add an item to a report and persist it."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        item = ReportItem(contractor="Civil", workers=10, zone="Zone A")
        saved = auto_save.auto_save_item(report, item, telegram_user="user1")
        assert saved.id is not None
        assert len(saved.items) == 1
        assert saved.items[0].contractor == "Civil"

    def test_auto_save_item_multiple_calls(self, auto_save: AutoSaveService):
        """Should handle multiple item additions."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        item1 = ReportItem(contractor="Civil", workers=10)
        saved = auto_save.auto_save_item(report, item1, telegram_user="user1")

        item2 = ReportItem(contractor="Electrical", workers=5)
        saved = auto_save.auto_save_item(saved, item2, telegram_user="user1")
        assert len(saved.items) == 2

    def test_auto_save_item_does_not_duplicate(self, auto_save: AutoSaveService):
        """Should not duplicate an item already in the list."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        item = ReportItem(contractor="Civil", workers=10)
        saved = auto_save.auto_save_item(report, item, telegram_user="user1")
        # Add same item object again
        saved2 = auto_save.auto_save_item(saved, item, telegram_user="user1")
        assert len(saved2.items) == 1  # Not duplicated

    # ------------------------------------------------------------------
    # remove_item_and_save
    # ------------------------------------------------------------------

    def test_remove_item_and_save(self, auto_save: AutoSaveService):
        """Should remove an item by index and persist."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        report.add_item(ReportItem(contractor="Civil", workers=10))
        report.add_item(ReportItem(contractor="Electrical", workers=5))
        saved = auto_save.save_draft(report, telegram_user="user1")

        # Remove first item
        saved2 = auto_save.remove_item_and_save(saved, 0, telegram_user="user1")
        assert len(saved2.items) == 1
        assert saved2.items[0].contractor == "Electrical"

    def test_remove_item_invalid_index(self, auto_save: AutoSaveService):
        """Should handle invalid index gracefully."""
        report = Report(
            date="2026-07-11",
            day="Ø§Ù„Ø³Ø¨Øª",
            telegram_user="user1",
        )
        saved = auto_save.save_draft(report, telegram_user="user1")
        # Remove with invalid index â€” should not raise
        result = auto_save.remove_item_and_save(saved, 5, telegram_user="user1")
        assert len(result.items) == 0

    # ------------------------------------------------------------------
    # Event Logging Integration
    # ------------------------------------------------------------------

    def test_save_draft_logs_event(self, auto_save: AutoSaveService, event_log_repo: EventLogRepository):
        """Should log an event on save_draft (report.created for new, draft.saved for update)."""
        report = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="user1")
        saved = auto_save.save_draft(report, telegram_user="user1")
        events = event_log_repo.get_by_object("report", saved.id)
        assert len(events) >= 1, "Should have at least 1 event"

        # Update should log draft.saved
        saved2 = auto_save.save_draft(saved, telegram_user="user1")
        events2 = event_log_repo.get_by_object("report", saved2.id)
        assert any(e.action == "draft.saved" for e in events2)

    def test_auto_save_item_logs_event(self, auto_save: AutoSaveService, event_log_repo: EventLogRepository):
        """Should log an event on auto_save_item."""
        report = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="user1")
        item = ReportItem(contractor="Civil", workers=10)
        saved = auto_save.auto_save_item(report, item, telegram_user="user1")
        events = event_log_repo.get_by_object("report", saved.id)
        assert len(events) >= 1, "Should have at least 1 event"
        # For new reports the first event is report.created
        assert any(e.action in ("report.created", "draft.saved") for e in events)

    # ------------------------------------------------------------------
    # Crash Resilience â€” verify_session_consistency
    # ------------------------------------------------------------------

    def test_verify_consistent(self, auto_save: AutoSaveService):
        """Should report consistent when session matches DB."""
        report = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="user1")
        report.add_item(ReportItem(contractor="Civil", workers=10))
        saved = auto_save.save_draft(report, telegram_user="user1")

        result = auto_save.verify_session_consistency(
            telegram_user="user1",
            session_date="2026-07-11",
            session_contractors=[{"contractor": "Civil", "workers": 10}],
        )
        assert result["consistent"] is True
        assert result["db_report"] is not None

    def test_verify_no_session_date(self, auto_save: AutoSaveService):
        """Should report inconsistent when no session date."""
        result = auto_save.verify_session_consistency(
            telegram_user="user1",
            session_date=None,
            session_contractors=[{"contractor": "Civil"}],
        )
        assert result["consistent"] is False

    def test_verify_mismatched_contractors(self, auto_save: AutoSaveService):
        """Should report inconsistency when contractor counts differ."""
        report = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="user1")
        report.add_item(ReportItem(contractor="Civil", workers=10))
        auto_save.save_draft(report, telegram_user="user1")

        result = auto_save.verify_session_consistency(
            telegram_user="user1",
            session_date="2026-07-11",
            session_contractors=[],  # DB has 1, session has 0
        )
        assert result["consistent"] is False
        assert len(result["differences"]) > 0

    def test_verify_no_db_report(self, auto_save: AutoSaveService):
        """Should report inconsistent when session has data but DB has no report."""
        result = auto_save.verify_session_consistency(
            telegram_user="user1",
            session_date="2026-07-11",
            session_contractors=[{"contractor": "Civil"}],
        )
        assert result["consistent"] is False

    # ------------------------------------------------------------------
    # Edge Cases
    # ------------------------------------------------------------------

    def test_save_draft_preserves_telegram_user(self, auto_save: AutoSaveService):
        """Should preserve the original telegram_user when updating."""
        report = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="original_user")
        saved = auto_save.save_draft(report, telegram_user="original_user")

        # Update with new Report object (no telegram_user set)
        update = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª")
        update.add_item(ReportItem(contractor="Civil", workers=10))

        # This goes through _update_existing â€” should keep original telegram_user
        result = auto_save.save_draft(update, telegram_user="original_user")
        assert result.telegram_user == "original_user"
        assert result.status == ReportStatus.DRAFT

    def test_save_draft_empty_items(self, auto_save: AutoSaveService):
        """Should save a report with no items."""
        report = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="user1")
        saved = auto_save.save_draft(report, telegram_user="user1")
        assert saved.id is not None
        assert len(saved.items) == 0

    def test_save_draft_idempotent(self, auto_save: AutoSaveService):
        """Saving the same report twice should not duplicate."""
        report = Report(date="2026-07-11", day="Ø§Ù„Ø³Ø¨Øª", telegram_user="user1")
        saved = auto_save.save_draft(report, telegram_user="user1")
        saved2 = auto_save.save_draft(saved, telegram_user="user1")
        assert saved2.id == saved.id
