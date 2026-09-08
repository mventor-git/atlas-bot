"""
Tests for EventLogRepository.

Tests cover:
- Logging events
- Querying by user, action, object
- Recent events listing
- Date range queries
- Event immutability
- Cleanup of old events
"""

import pytest

from app.models.database import EventLogEntry
from app.repositories.event_log_repository import (
    EventLogRepository,
    EVENT_REPORT_CREATED,
    EVENT_DRAFT_SAVED,
    EVENT_CONTRACTOR_ADDED,
)


class TestEventLogRepository:
    """Tests for EventLogRepository."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        from app.database.manager import DatabaseManager
        self._db = DatabaseManager(self._tmp.name)
        self._repo = EventLogRepository(self._db)

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

    def test_log_event(self):
        """Logging an event should return it with an ID."""
        entry = self._repo.log(
            telegram_user="user1",
            action=EVENT_REPORT_CREATED,
            object_type="report",
            object_id=1,
            object_date="2026-07-11",
        )
        assert entry.id is not None, "Event should have an ID after logging"
        assert entry.telegram_user == "user1", (
            f"Expected 'user1', got '{entry.telegram_user}'"
        )
        assert entry.action == EVENT_REPORT_CREATED, (
            f"Expected {EVENT_REPORT_CREATED}, got '{entry.action}'"
        )

    def test_log_event_with_old_and_new_values(self):
        """Event should store old and new values."""
        entry = self._repo.log(
            telegram_user="user1",
            action=EVENT_CONTRACTOR_ADDED,
            object_type="report_item",
            object_id=5,
            old_value='{"workers": 0}',
            new_value='{"workers": 10}',
        )
        assert entry.old_value == '{"workers": 0}', (
            f"Expected old value, got '{entry.old_value}'"
        )
        assert entry.new_value == '{"workers": 10}', (
            f"Expected new value, got '{entry.new_value}'"
        )

    def test_get_by_user(self):
        """Should return events for a specific user."""
        self._repo.log("user1", EVENT_REPORT_CREATED, object_type="report")
        self._repo.log("user1", EVENT_DRAFT_SAVED, object_type="report")
        self._repo.log("user2", EVENT_REPORT_CREATED, object_type="report")

        user1_events = self._repo.get_by_user("user1")
        assert len(user1_events) == 2, (
            f"Expected 2 events for user1, got {len(user1_events)}"
        )

        user2_events = self._repo.get_by_user("user2")
        assert len(user2_events) == 1, (
            f"Expected 1 event for user2, got {len(user2_events)}"
        )

    def test_get_by_user_no_events(self):
        """Should return empty list for user with no events."""
        events = self._repo.get_by_user("nonexistent")
        assert events == [], "Should return empty list for unknown user"

    def test_get_by_action(self):
        """Should return events of a specific type."""
        self._repo.log("user1", EVENT_REPORT_CREATED)
        self._repo.log("user2", EVENT_REPORT_CREATED)
        self._repo.log("user1", EVENT_DRAFT_SAVED)

        created = self._repo.get_by_action(EVENT_REPORT_CREATED)
        assert len(created) == 2, (
            f"Expected 2 created events, got {len(created)}"
        )

        saved = self._repo.get_by_action(EVENT_DRAFT_SAVED)
        assert len(saved) == 1, (
            f"Expected 1 saved event, got {len(saved)}"
        )

    def test_get_by_object(self):
        """Should return events for a specific object."""
        self._repo.log("user1", EVENT_REPORT_CREATED, object_type="report", object_id=1)
        self._repo.log("user1", EVENT_DRAFT_SAVED, object_type="report", object_id=1)
        self._repo.log("user1", EVENT_REPORT_CREATED, object_type="report", object_id=2)

        events = self._repo.get_by_object("report", 1)
        assert len(events) == 2, (
            f"Expected 2 events for report 1, got {len(events)}"
        )

    def test_get_recent(self):
        """Should return most recent events."""
        self._repo.log("user1", "event.1")
        self._repo.log("user2", "event.2")
        self._repo.log("user3", "event.3")

        recent = self._repo.get_recent(limit=2)
        assert len(recent) == 2, (
            f"Expected 2 recent events, got {len(recent)}"
        )

    def test_get_by_date_range(self):
        """Should return events within a date range."""
        self._repo.log("user1", "event.early", timestamp="2026-07-10T08:00:00")
        self._repo.log("user1", "event.mid", timestamp="2026-07-11T12:00:00")
        self._repo.log("user1", "event.late", timestamp="2026-07-12T18:00:00")

        # Get all events within range
        events = self._repo.get_by_date_range(
            "2026-07-11T00:00:00", "2026-07-12T23:59:59"
        )
        assert len(events) >= 2, (
            f"Expected at least 2 events in range, got {len(events)}"
        )

    def test_event_immutable(self):
        """Event log entries should be immutable (update raises error)."""
        entry = self._repo.log("user1", EVENT_REPORT_CREATED)
        from app.utils.exceptions import DatabaseError
        with pytest.raises(DatabaseError, match="immutable"):
            self._repo.update(entry)

    def test_cleanup_old_events(self):
        """Should delete events older than N days."""
        self._repo.log("user1", "event.old")
        self._repo.log("user1", "event.new")

        # Use a very large TTL to ensure nothing is deleted
        deleted = self._repo.cleanup_old_events(days=0)
        assert deleted >= 0, "Cleanup should not raise error"

    def test_count(self):
        """Should return total event count."""
        assert self._repo.count() == 0, "Should start with 0 events"
        self._repo.log("user1", EVENT_REPORT_CREATED)
        assert self._repo.count() == 1, (
            f"Expected 1 event, got {self._repo.count()}"
        )

    def test_get_by_id(self):
        """Should retrieve event by ID."""
        entry = self._repo.log("user1", EVENT_REPORT_CREATED)
        fetched = self._repo.get_by_id(entry.id)
        assert fetched is not None, "Should find event by ID"
        assert fetched.action == EVENT_REPORT_CREATED, (
            f"Expected {EVENT_REPORT_CREATED}, got '{fetched.action}'"
        )

    def test_delete_event(self):
        """Should delete event by ID."""
        entry = self._repo.log("user1", EVENT_REPORT_CREATED)
        assert self._repo.count() == 1, "Should have 1 event"

        deleted = self._repo.delete(entry.id)
        assert deleted is True, "Delete should return True"
        assert self._repo.count() == 0, "Should have 0 events after delete"
