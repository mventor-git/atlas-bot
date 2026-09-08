"""
Tests for EventLogService.

Tests cover:
- Convenience methods for all event types
- Query methods (by user, action, object, date range)
- Cleanup of old events
- Integration with ReportRepository (events logged on add/update/delete)
- Backward compatibility (ReportRepository without EventLogRepository)
"""

import json
import pytest

from app.models.database import Report, ReportItem, ReportStatus, EventLogEntry
from app.services.event_log_service import EventLogService
from app.repositories.event_log_repository import (
    EVENT_REPORT_CREATED, EVENT_REPORT_DELETED, EVENT_REPORT_FINALIZED,
    EVENT_DRAFT_SAVED, EVENT_CONTRACTOR_ADDED, EVENT_CONTRACTOR_REMOVED,
    EVENT_WORKERS_CHANGED, EVENT_PDF_GENERATED, EVENT_REPORT_LOCKED,
    EVENT_REPORT_UNLOCKED, EVENT_VERSION_CREATED, EVENT_VERSION_RESTORED,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.event_log_repository import EventLogRepository


class TestEventLogService:
    """Tests for EventLogService convenience methods."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        from app.database.manager import DatabaseManager
        self._db = DatabaseManager(self._tmp.name)
        self._service = EventLogService(self._db)
        self._event_log_repo = EventLogRepository(self._db)

    def teardown_method(self):
        """Clean up after each test."""
        try:
            self._db.close()
        except Exception:
            pass
        import time
        import os
        for attempt in range(5):
            try:
                os.unlink(self._tmp.name)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1)

    def _make_report(self, date="2026-07-11", status=ReportStatus.FINAL) -> Report:
        """Helper to create a Report object (without persisting)."""
        report = Report(
            date=date,
            day="السبت",
            status=status,
            telegram_user="user1",
        )
        report.id = 1
        report.add_item(ReportItem(contractor="Civil Co", workers=10, zone="Zone A"))
        return report

    def test_log_report_created(self):
        """log_report_created should create a report.created event."""
        report = self._make_report()
        entry = self._service.log_report_created("user1", report)
        assert entry.id is not None, "Entry should have an ID"
        assert entry.action == EVENT_REPORT_CREATED, (
            f"Expected {EVENT_REPORT_CREATED}, got '{entry.action}'"
        )
        assert entry.object_type == "report", (
            f"Expected 'report', got '{entry.object_type}'"
        )

    def test_log_draft_saved(self):
        """log_draft_saved should create a draft.saved event."""
        report = self._make_report()
        entry = self._service.log_draft_saved("user1", report, old_value='{"old":1}', new_value='{"new":2}')
        assert entry.action == EVENT_DRAFT_SAVED, (
            f"Expected {EVENT_DRAFT_SAVED}, got '{entry.action}'"
        )
        assert entry.old_value == '{"old":1}', "Should preserve old_value"
        assert entry.new_value == '{"new":2}', "Should preserve new_value"

    def test_log_report_finalized(self):
        """log_report_finalized should create a report.finalized event."""
        report = self._make_report()
        entry = self._service.log_report_finalized("user1", report)
        assert entry.action == EVENT_REPORT_FINALIZED, (
            f"Expected {EVENT_REPORT_FINALIZED}, got '{entry.action}'"
        )

    def test_log_report_deleted(self):
        """log_report_deleted should create a report.deleted event."""
        report = self._make_report()
        entry = self._service.log_report_deleted("user1", report)
        assert entry.action == EVENT_REPORT_DELETED, (
            f"Expected {EVENT_REPORT_DELETED}, got '{entry.action}'"
        )
        assert entry.old_value is not None, "Should capture old_value summary"

    def test_log_report_locked(self):
        """log_report_locked should create a report.locked event."""
        report = self._make_report()
        entry = self._service.log_report_locked("user1", report)
        assert entry.action == EVENT_REPORT_LOCKED, (
            f"Expected {EVENT_REPORT_LOCKED}, got '{entry.action}'"
        )

    def test_log_report_unlocked(self):
        """log_report_unlocked should create a report.unlocked event."""
        report = self._make_report()
        entry = self._service.log_report_unlocked("user1", report)
        assert entry.action == EVENT_REPORT_UNLOCKED, (
            f"Expected {EVENT_REPORT_UNLOCKED}, got '{entry.action}'"
        )

    def test_log_contractor_added(self):
        """log_contractor_added should create a contractor.added event."""
        report = self._make_report()
        entry = self._service.log_contractor_added("user1", report, "Civil Co")
        assert entry.action == EVENT_CONTRACTOR_ADDED, (
            f"Expected {EVENT_CONTRACTOR_ADDED}, got '{entry.action}'"
        )
        assert entry.new_value == "Civil Co", (
            f"Expected 'Civil Co', got '{entry.new_value}'"
        )

    def test_log_contractor_removed(self):
        """log_contractor_removed should create a contractor.removed event."""
        report = self._make_report()
        entry = self._service.log_contractor_removed("user1", report, "Civil Co")
        assert entry.action == EVENT_CONTRACTOR_REMOVED, (
            f"Expected {EVENT_CONTRACTOR_REMOVED}, got '{entry.action}'"
        )
        assert entry.old_value == "Civil Co", (
            f"Expected 'Civil Co', got '{entry.old_value}'"
        )

    def test_log_workers_changed(self):
        """log_workers_changed should create a workers.changed event."""
        report = self._make_report()
        entry = self._service.log_workers_changed("user1", report, "Civil Co", 5, 10)
        assert entry.action == EVENT_WORKERS_CHANGED, (
            f"Expected {EVENT_WORKERS_CHANGED}, got '{entry.action}'"
        )
        assert entry.old_value == "5", (
            f"Expected '5', got '{entry.old_value}'"
        )
        assert entry.new_value == "10", (
            f"Expected '10', got '{entry.new_value}'"
        )

    def test_log_pdf_generated(self):
        """log_pdf_generated should create a pdf.generated event."""
        report = self._make_report()
        entry = self._service.log_pdf_generated("user1", report, "/path/to/report.pdf")
        assert entry.action == EVENT_PDF_GENERATED, (
            f"Expected {EVENT_PDF_GENERATED}, got '{entry.action}'"
        )
        assert entry.new_value == "/path/to/report.pdf", "Should store PDF path"

    def test_log_version_created(self):
        """log_version_created should create a version.created event."""
        report = self._make_report()
        entry = self._service.log_version_created("user1", report, version_number=3)
        assert entry.action == EVENT_VERSION_CREATED, (
            f"Expected {EVENT_VERSION_CREATED}, got '{entry.action}'"
        )
        assert "v3" in entry.new_value, "Should reference version number"

    def test_log_version_restored(self):
        """log_version_restored should create a version.restored event."""
        report = self._make_report()
        entry = self._service.log_version_restored("user1", report, version_number=2)
        assert entry.action == EVENT_VERSION_RESTORED, (
            f"Expected {EVENT_VERSION_RESTORED}, got '{entry.action}'"
        )
        assert "v2" in entry.old_value, "Should reference version number"

    def test_direct_log(self):
        """Direct log() call should work as a pass-through."""
        entry = self._service.log(
            telegram_user="user1",
            action="custom.action",
            object_type="test",
            object_id=42,
        )
        assert entry.telegram_user == "user1", (
            f"Expected 'user1', got '{entry.telegram_user}'"
        )
        assert entry.action == "custom.action", (
            f"Expected 'custom.action', got '{entry.action}'"
        )
        assert entry.object_id == 42, (
            f"Expected 42, got {entry.object_id}"
        )


class TestEventLogServiceQueries:
    """Tests for EventLogService query methods."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        from app.database.manager import DatabaseManager
        self._db = DatabaseManager(self._tmp.name)
        self._event_log_repo = EventLogRepository(self._db)
        self._service = EventLogService(self._db)
        # Seed some events
        self._event_log_repo.log("user1", EVENT_REPORT_CREATED, object_type="report", object_id=1)
        self._event_log_repo.log("user1", EVENT_DRAFT_SAVED, object_type="report", object_id=1)
        self._event_log_repo.log("user2", EVENT_REPORT_CREATED, object_type="report", object_id=2)

    def teardown_method(self):
        """Clean up after each test."""
        try:
            self._db.close()
        except Exception:
            pass
        import time
        import os
        for attempt in range(5):
            try:
                os.unlink(self._tmp.name)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1)

    def test_get_by_user(self):
        """get_by_user should return events for the specified user."""
        events = self._service.get_by_user("user1")
        assert len(events) == 2, (
            f"Expected 2 events for user1, got {len(events)}"
        )

    def test_get_by_user_empty(self):
        """get_by_user should return empty list for unknown user."""
        events = self._service.get_by_user("nonexistent")
        assert events == [], "Should return empty list"

    def test_get_by_action(self):
        """get_by_action should return events of the specified type."""
        events = self._service.get_by_action(EVENT_REPORT_CREATED)
        assert len(events) == 2, (
            f"Expected 2 created events, got {len(events)}"
        )

    def test_get_by_object(self):
        """get_by_object should return events for the specified object."""
        events = self._service.get_by_object("report", 1)
        assert len(events) == 2, (
            f"Expected 2 events for report 1, got {len(events)}"
        )

    def test_get_recent(self):
        """get_recent should return the most recent events."""
        events = self._service.get_recent(limit=2)
        assert len(events) == 2, (
            f"Expected 2 recent events, got {len(events)}"
        )

    def test_get_by_date_range(self):
        """get_by_date_range should return events within range."""
        events = self._service.get_by_date_range("2020-01-01T00:00:00", "2099-12-31T23:59:59")
        assert len(events) >= 3, (
            f"Expected at least 3 events in range, got {len(events)}"
        )

    def test_cleanup_old_events(self):
        """cleanup_old_events should not raise and return a count."""
        deleted = self._service.cleanup_old_events(days=0)
        assert deleted >= 0, "Cleanup should return non-negative count"


class TestEventLogIntegration:
    """Integration tests: ReportRepository + EventLogRepository."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        from app.database.manager import DatabaseManager
        self._db = DatabaseManager(self._tmp.name)
        self._event_log_repo = EventLogRepository(self._db)
        self._report_repo = ReportRepository(self._db, event_log_repo=self._event_log_repo)

    def teardown_method(self):
        """Clean up after each test."""
        try:
            self._db.close()
        except Exception:
            pass
        import time
        import os
        for attempt in range(5):
            try:
                os.unlink(self._tmp.name)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1)

    def _create_report(self, date="2026-07-11") -> Report:
        """Helper to create and persist a draft report."""
        report = Report(
            date=date,
            day="السبت",
            status=ReportStatus.DRAFT,
            telegram_user="user1",
        )
        report.add_item(ReportItem(contractor="Civil Co", workers=10, zone="Zone A"))
        return self._report_repo.add(report)

    def test_add_creates_event(self):
        """Adding a report should create a report.created event."""
        report = self._create_report()
        events = self._event_log_repo.get_by_object("report", report.id)
        assert len(events) >= 1, "Add should create at least 1 event"
        assert events[0].action == EVENT_REPORT_CREATED, (
            f"Expected {EVENT_REPORT_CREATED}, got '{events[0].action}'"
        )

    def test_add_event_has_correct_user(self):
        """The event should reference the correct telegram_user."""
        report = Report(
            date="2026-07-12",
            day="الأحد",
            status=ReportStatus.FINAL,
            telegram_user="special_user",
        )
        report = self._report_repo.add(report)
        events = self._event_log_repo.get_by_object("report", report.id)
        assert events[0].telegram_user == "special_user", (
            f"Expected 'special_user', got '{events[0].telegram_user}'"
        )

    def test_update_creates_event(self):
        """Updating a report should create a draft.saved event."""
        report = self._create_report()
        # Clear log
        report.status = ReportStatus.DRAFT
        self._report_repo.update(report)
        events = self._event_log_repo.get_by_object("report", report.id)
        updated_events = [e for e in events if e.action == EVENT_DRAFT_SAVED]
        assert len(updated_events) >= 1, (
            f"Update should create {EVENT_DRAFT_SAVED} events"
        )

    def test_update_event_has_old_and_new_values(self):
        """Update event should include old and new state summaries."""
        report = self._create_report()
        report.status = ReportStatus.DRAFT
        self._report_repo.update(report)
        events = self._event_log_repo.get_by_object("report", report.id)
        draft_events = [e for e in events if e.action == EVENT_DRAFT_SAVED]
        assert len(draft_events) >= 1, "Should have at least 1 draft.saved event"
        event = draft_events[0]
        # old_value should be set from the report before update
        assert event.old_value is not None, "Should have old_value"
        assert event.new_value is not None, "Should have new_value"

    def test_delete_creates_event(self):
        """Deleting a report should create a report.deleted event."""
        report = self._create_report()
        report_id = report.id
        self._report_repo.delete(report_id)
        events = self._event_log_repo.get_by_object("report", report_id)
        delete_events = [e for e in events if e.action == EVENT_REPORT_DELETED]
        assert len(delete_events) >= 1, (
            f"Delete should create {EVENT_REPORT_DELETED} events"
        )

    def test_delete_event_has_old_value(self):
        """Delete event should include the report summary as old_value."""
        report = self._create_report()
        report_id = report.id
        self._report_repo.delete(report_id)
        events = self._event_log_repo.get_by_object("report", report_id)
        delete_events = [e for e in events if e.action == EVENT_REPORT_DELETED]
        assert delete_events[0].old_value is not None, (
            "Delete event should have old_value with report summary"
        )

    def test_no_event_log_still_works(self):
        """ReportRepository without EventLogRepository should work unchanged."""
        repo = ReportRepository(self._db)  # No event_log_repo passed
        report = Report(date="2026-07-20", day="الاثنين", telegram_user="user1")
        saved = repo.add(report)
        assert saved.id is not None, "Report should be added even without event log"

        repo.update(saved)
        repo.delete(saved.id)
        # Should not raise any errors

    def test_replace_report_logs_event(self):
        """Replace_report should create events for delete+add."""
        report1 = self._create_report("2026-07-11")
        report2 = Report(
            date="2026-07-11",
            day="السبت",
            status=ReportStatus.DRAFT,
            telegram_user="user2",
        )
        report2.add_item(ReportItem(contractor="Electric Inc", workers=5))
        replaced = self._report_repo.replace_report(report2)

        events = self._event_log_repo.get_by_object("report", replaced.id)
        assert len(events) >= 1, "Replace should create events"
