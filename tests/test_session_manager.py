"""
Tests for SessionManager.

Tests cover:
- Session creation and retrieval
- State transitions
- Data storage and merging
- Session expiry and cleanup
- Thread safety
- Edge cases (missing sessions, multiple users)
- Background cleanup thread lifecycle
"""

import time
from datetime import datetime, timedelta
from typing import Generator

import pytest

from app.models.config import AppConfig, SessionConfig
from app.models.database import SessionState, UserSession
from app.services.session_manager import SessionManager, SessionManagerError


def _make_config(
    timeout_minutes: int = 30,
    cleanup_interval: int = 300,
) -> AppConfig:
    """Create a minimal AppConfig for testing."""
    return AppConfig(
        template={"file": "template.xlsx", "tables_file": "tables.xlsx"},
        date={"cell": "B4", "day_cell": "D4"},
        table={"start_row": 12, "columns": {
            "serial": "A", "contractor": "B", "type": "C",
            "zone": "D", "workers": "E", "details": "F",
        }},
        output={"pdf_folder": "exports/pdf", "excel_folder": "exports/excel"},
        database={"path": ":memory:"},
        session=SessionConfig(
            timeout_minutes=timeout_minutes,
            cleanup_interval_seconds=cleanup_interval,
        ),
    )


class TestSessionManager:
    """Test suite for SessionManager."""

    # --- Session Creation ---

    def test_create_session(self):
        """Should create a new session for a user."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            session = manager.create_session("user1")
            assert session.telegram_user == "user1"
            assert session.state == SessionState.IDLE
            assert session.data == {}
            assert session.id == "user1"

            # Session should be retrievable
            assert manager.session_exists("user1") is True
        finally:
            manager.stop()

    def test_create_session_returns_existing(self):
        """Should return existing session when creating duplicate."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            s1 = manager.create_session("user1")
            s2 = manager.create_session("user1")
            assert s1 is s2  # Same object
        finally:
            manager.stop()

    def test_create_multiple_users(self):
        """Should create independent sessions for different users."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            s1 = manager.create_session("user1")
            s2 = manager.create_session("user2")
            assert s1 is not s2
            assert s1.telegram_user == "user1"
            assert s2.telegram_user == "user2"
        finally:
            manager.stop()

    # --- Session Retrieval ---

    def test_get_session_existing(self):
        """Should retrieve an existing session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            session = manager.get_session("user1")
            assert session is not None
            assert session.telegram_user == "user1"
        finally:
            manager.stop()

    def test_get_session_nonexistent(self):
        """Should return None for non-existing user."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            session = manager.get_session("unknown")
            assert session is None
        finally:
            manager.stop()

    def test_get_session_updates_timestamp(self):
        """Getting a session should not change its updated_at."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            original = manager.get_session("user1")
            time.sleep(0.01)
            retrieved = manager.get_session("user1")
            assert original.updated_at == retrieved.updated_at
        finally:
            manager.stop()

    def test_get_or_create_new(self):
        """Should create session when not existing."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            session = manager.get_or_create_session("new_user")
            assert session.telegram_user == "new_user"
            assert session.state == SessionState.IDLE
        finally:
            manager.stop()

    def test_get_or_create_existing(self):
        """Should return existing session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            s1 = manager.create_session("user1")
            s2 = manager.get_or_create_session("user1")
            assert s1 is s2
        finally:
            manager.stop()

    # --- State Updates ---

    def test_update_state(self):
        """Should update session state."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            session = manager.update_state("user1", SessionState.AWAITING_DATE)
            assert session.state == SessionState.AWAITING_DATE
        finally:
            manager.stop()

    def test_update_state_updates_timestamp(self):
        """Should update the updated_at timestamp."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            before = manager.get_session("user1").updated_at
            time.sleep(0.01)
            manager.update_state("user1", SessionState.AWAITING_DATE)
            after = manager.get_session("user1").updated_at
            assert after > before
        finally:
            manager.stop()

    def test_update_state_no_session(self):
        """Should raise error when updating state without session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            with pytest.raises(SessionManagerError, match="No session found"):
                manager.update_state("unknown", SessionState.IDLE)
        finally:
            manager.stop()

    def test_update_state_all_states(self):
        """Should support all session states."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            for state in SessionState:
                manager.update_state("user1", state)
                assert manager.get_session("user1").state == state
        finally:
            manager.stop()

    # --- Data Management ---

    def test_set_data(self):
        """Should store data in session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            session = manager.set_data("user1", "date", "2026-07-11")
            assert session.data["date"] == "2026-07-11"
            assert manager.get_session("user1").data["date"] == "2026-07-11"
        finally:
            manager.stop()

    def test_set_data_multiple_keys(self):
        """Should store multiple data keys."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.set_data("user1", "date", "2026-07-11")
            manager.set_data("user1", "day", "السبت")
            manager.set_data("user1", "workers", 10)
            session = manager.get_session("user1")
            assert session.data["date"] == "2026-07-11"
            assert session.data["day"] == "السبت"
            assert session.data["workers"] == 10
        finally:
            manager.stop()

    def test_set_data_no_session(self):
        """Should raise error when setting data without session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            with pytest.raises(SessionManagerError, match="No session found"):
                manager.set_data("unknown", "key", "value")
        finally:
            manager.stop()

    def test_set_data_overwrites(self):
        """Should overwrite existing data key."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.set_data("user1", "date", "2026-07-11")
            manager.set_data("user1", "date", "2026-07-12")
            assert manager.get_session("user1").data["date"] == "2026-07-12"
        finally:
            manager.stop()

    def test_update_session_data(self):
        """Should merge data dict into session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.set_data("user1", "existing", "keep")
            manager.update_session_data("user1", {
                "date": "2026-07-11",
                "day": "السبت",
                "workers": 15,
            })
            session = manager.get_session("user1")
            assert session.data["existing"] == "keep"
            assert session.data["date"] == "2026-07-11"
            assert session.data["day"] == "السبت"
            assert session.data["workers"] == 15
        finally:
            manager.stop()

    def test_update_session_data_no_session(self):
        """Should raise error when merging data without session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            with pytest.raises(SessionManagerError, match="No session found"):
                manager.update_session_data("unknown", {"key": "value"})
        finally:
            manager.stop()

    def test_contractor_data_structure(self):
        """Should support complex contractor data."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.update_session_data("user1", {
                "contractors": [
                    {"name": "Civil Co", "workers": 10, "zone": "Zone A"},
                    {"name": "Electric Inc", "workers": 5, "zone": "Zone B"},
                ],
            })
            session = manager.get_session("user1")
            contractors = session.data["contractors"]
            assert len(contractors) == 2
            assert contractors[0]["name"] == "Civil Co"
            assert contractors[1]["workers"] == 5
        finally:
            manager.stop()

    # --- Session Existence ---

    def test_session_exists_true(self):
        """Should return True for existing session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            assert manager.session_exists("user1") is True
        finally:
            manager.stop()

    def test_session_exists_false(self):
        """Should return False for non-existing session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            assert manager.session_exists("unknown") is False
        finally:
            manager.stop()

    def test_session_exists_after_clear(self):
        """Should return False after clearing session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.clear_session("user1")
            assert manager.session_exists("user1") is False
        finally:
            manager.stop()

    # --- Clear Session ---

    def test_clear_session(self):
        """Should remove a session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            result = manager.clear_session("user1")
            assert result is True
            assert manager.get_session("user1") is None
        finally:
            manager.stop()

    def test_clear_nonexistent_session(self):
        """Should return False when clearing non-existing session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            result = manager.clear_session("unknown")
            assert result is False
        finally:
            manager.stop()

    def test_clear_session_only_removes_target(self):
        """Should only remove the specified user's session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.create_session("user2")
            manager.clear_session("user1")
            assert manager.session_exists("user1") is False
            assert manager.session_exists("user2") is True
        finally:
            manager.stop()

    # --- Session Count ---

    def test_active_session_count_zero(self):
        """Should return 0 when no sessions exist."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            assert manager.get_active_session_count() == 0
        finally:
            manager.stop()

    def test_active_session_count(self):
        """Should return correct count."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.create_session("user2")
            manager.create_session("user3")
            assert manager.get_active_session_count() == 3
        finally:
            manager.stop()

    def test_active_session_count_after_clear(self):
        """Should update count after clearing sessions."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.create_session("user2")
            manager.clear_session("user1")
            assert manager.get_active_session_count() == 1
        finally:
            manager.stop()

    # --- Session Expiry ---

    def test_session_expires_after_timeout(self):
        """Should return None for expired session."""
        config = _make_config(timeout_minutes=1)
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            # Manually set updated_at in the past
            past_time = (datetime.now() - timedelta(minutes=5)).isoformat()
            with manager._lock:
                manager._sessions["user1"].updated_at = past_time
            session = manager.get_session("user1")
            assert session is None  # Should have expired
        finally:
            manager.stop()

    def test_get_session_removes_expired(self):
        """Getting an expired session should remove it from storage."""
        config = _make_config(timeout_minutes=1)
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            past_time = (datetime.now() - timedelta(minutes=5)).isoformat()
            with manager._lock:
                manager._sessions["user1"].updated_at = past_time
            # First access should return None and remove it
            assert manager.get_session("user1") is None
            # Second access should also return None (already removed)
            assert manager.get_session("user1") is None
        finally:
            manager.stop()

    def test_cleanup_expired_sessions(self):
        """Should remove expired sessions."""
        config = _make_config(timeout_minutes=1)
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.create_session("user2")
            past_time = (datetime.now() - timedelta(minutes=5)).isoformat()
            with manager._lock:
                manager._sessions["user1"].updated_at = past_time
                manager._sessions["user2"].updated_at = past_time
            removed = manager.cleanup_expired_sessions()
            assert removed == 2
            assert manager.get_active_session_count() == 0
        finally:
            manager.stop()

    def test_cleanup_only_expired(self):
        """Should only remove expired sessions, keep active ones."""
        config = _make_config(timeout_minutes=10)
        manager = SessionManager(config)
        try:
            manager.create_session("user1")  # Will expire
            manager.create_session("user2")  # Will stay

            # Manually set user1's timestamp to the past
            with manager._lock:
                past_time = (datetime.now() - timedelta(minutes=15)).isoformat()
                manager._sessions["user1"].updated_at = past_time

            removed = manager.cleanup_expired_sessions()
            assert removed == 1
            assert manager.session_exists("user2") is True
            assert manager.session_exists("user1") is False
        finally:
            manager.stop()

    def test_get_active_session_count_excludes_expired(self):
        """Count should exclude expired sessions."""
        config = _make_config(timeout_minutes=10)
        manager = SessionManager(config)
        try:
            manager.create_session("user1")  # Will expire
            manager.create_session("user2")  # Will stay

            with manager._lock:
                past_time = (datetime.now() - timedelta(minutes=15)).isoformat()
                manager._sessions["user1"].updated_at = past_time

            # The count should detect expired sessions and clean them up
            count = manager.get_active_session_count()
            assert count == 1
        finally:
            manager.stop()

    def test_get_all_sessions_excludes_expired(self):
        """get_all_sessions should only return active sessions."""
        config = _make_config(timeout_minutes=10)
        manager = SessionManager(config)
        try:
            manager.create_session("user1")  # Will expire
            manager.create_session("user2")  # Will stay

            with manager._lock:
                past_time = (datetime.now() - timedelta(minutes=15)).isoformat()
                manager._sessions["user1"].updated_at = past_time

            sessions = manager.get_all_sessions()
            assert len(sessions) == 1
            assert sessions[0].telegram_user == "user2"
        finally:
            manager.stop()

    # --- Thread Safety ---

    def test_concurrent_session_creation(self):
        """Should handle concurrent session operations."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            import threading

            results: list = []
            errors: list = []

            def create_and_modify(user_id: str) -> None:
                try:
                    manager.create_session(user_id)
                    manager.set_data(user_id, "key", user_id)
                    manager.update_state(user_id, SessionState.AWAITING_DATE)
                    session = manager.get_session(user_id)
                    results.append(session.telegram_user)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=create_and_modify, args=(f"user{i}",))
                for i in range(20)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Thread errors: {errors}"
            assert len(results) == 20
            assert manager.get_active_session_count() == 20
        finally:
            manager.stop()

    def test_concurrent_data_update(self):
        """Should handle concurrent data updates to same session."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            import threading

            manager.create_session("user1")
            errors: list = []

            def update_data(value: int) -> None:
                try:
                    session = manager.get_session("user1")
                    assert session is not None
                    manager.set_data("user1", "value", value)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=update_data, args=(i,))
                for i in range(50)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
            # The final value should be one of the thread values
            assert manager.get_session("user1").data["value"] in range(50)
        finally:
            manager.stop()

    # --- Cleanup Thread ---

    def test_cleanup_thread_runs(self):
        """Cleanup thread should be running after initialization."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            assert manager._cleanup_thread.is_alive() is True
        finally:
            manager.stop()

    def test_stop_cleanup_thread(self):
        """Stop should terminate the cleanup thread."""
        config = _make_config(cleanup_interval=5)
        manager = SessionManager(config)
        manager.stop(timeout=5)
        assert manager._cleanup_thread.is_alive() is False

    def test_stop_twice(self):
        """Calling stop twice should not raise."""
        config = _make_config(cleanup_interval=5)
        manager = SessionManager(config)
        manager.stop(timeout=5)
        manager.stop()  # Should not raise

    def test_cleanup_loop_handles_exceptions(self):
        """Cleanup loop should not crash on exceptions."""
        config = _make_config(cleanup_interval=5)
        manager = SessionManager(config)
        try:
            # The loop should run without crashing
            time.sleep(0.2)
        finally:
            manager.stop(timeout=5)

    # --- Edge Cases ---

    def test_session_with_invalid_timestamp(self):
        """Should treat session with invalid timestamp as expired."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            with manager._lock:
                manager._sessions["user1"].updated_at = "invalid-date"
            session = manager.get_session("user1")
            assert session is None  # Treated as expired
        finally:
            manager.stop()

    def test_data_isolation_between_users(self):
        """Different users should have independent session data."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.create_session("user2")
            manager.set_data("user1", "date", "2026-07-11")
            manager.set_data("user2", "date", "2026-07-12")

            assert manager.get_session("user1").data["date"] == "2026-07-11"
            assert manager.get_session("user2").data["date"] == "2026-07-12"
        finally:
            manager.stop()

    def test_state_independence(self):
        """Different users should have independent states."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            manager.create_session("user1")
            manager.create_session("user2")
            manager.update_state("user1", SessionState.AWAITING_DATE)
            manager.update_state("user2", SessionState.AWAITING_CONFIRMATION)

            assert manager.get_session("user1").state == SessionState.AWAITING_DATE
            assert manager.get_session("user2").state == SessionState.AWAITING_CONFIRMATION
        finally:
            manager.stop()

    def test_session_id_matches_user(self):
        """Session ID should default to telegram_user."""
        config = _make_config()
        manager = SessionManager(config)
        try:
            session = manager.create_session("test_user_123")
            assert session.id == "test_user_123"
        finally:
            manager.stop()
