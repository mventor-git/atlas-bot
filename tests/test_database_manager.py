"""
Tests for DatabaseManager.

Tests cover:
- Database file creation
- Schema initialization (tables, indexes)
- Connection handling (thread-local)
- execute, executemany, commit, rollback
- table_exists
- Error handling
- Close and cleanup
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.utils.exceptions import DatabaseError


class TestDatabaseManager:
    """Test suite for DatabaseManager."""

    @pytest.fixture
    def db_path(self):
        """Create a temporary database file path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        """Create a DatabaseManager instance."""
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close()

    def test_creates_database_file(self, db_path: Path):
        """Should create the database file on init."""
        manager = DatabaseManager(str(db_path))
        assert db_path.exists(), "Database file should exist after init"
        manager.close_all()

    def test_creates_parent_directory(self):
        """Should create parent directories if they don't exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested_path = Path(tmp_dir) / "nested" / "deep" / "test.db"
            manager = DatabaseManager(str(nested_path))
            assert nested_path.exists(), "Nested database file should exist after init"
            assert nested_path.parent.exists(), "Parent directory should exist after init"
            manager.close_all()
            os.remove(str(nested_path))
            for parent in [nested_path.parent, nested_path.parent.parent]:
                if parent.exists():
                    parent.rmdir()

    def test_schema_initialization(self, db_manager: DatabaseManager):
        """Should create tables on initialization."""
        assert db_manager.table_exists("reports"), "reports table should exist after init"
        assert db_manager.table_exists("report_items"), "report_items table should exist after init"

    def test_indexes_created(self, db_manager: DatabaseManager):
        """Should create indexes on initialization."""
        cursor = db_manager.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_reports_date'"
        )
        assert cursor.fetchone() is not None, "idx_reports_date index should exist"

        cursor = db_manager.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_report_items_report_id'"
        )
        assert cursor.fetchone() is not None, "idx_report_items_report_id index should exist"

    def test_execute_insert_and_select(self, db_manager: DatabaseManager):
        """Should execute SQL insert and select."""
        db_manager.execute(
            "INSERT INTO reports (date, day, status, created_at) VALUES (?, ?, ?, ?)",
            ("2026-07-11", "السبت", "final", "2026-07-11T10:00:00"),
        )
        db_manager.commit()

        cursor = db_manager.execute("SELECT * FROM reports")
        rows = cursor.fetchall()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert rows[0]["date"] == "2026-07-11", f"Expected date 2026-07-11, got {rows[0]['date']}"

    def test_executemany(self, db_manager: DatabaseManager):
        """Should execute multiple inserts."""
        db_manager.execute(
            "INSERT INTO reports (date, day, status, created_at) VALUES (?, ?, ?, ?)",
            ("2026-07-11", "السبت", "final", "2026-07-11T10:00:00"),
        )
        report_id = db_manager.execute("SELECT last_insert_rowid()").fetchone()[0]

        items = [
            (report_id, "Civil", "Civil Type", "Zone A", 10, "5 Mason, 5 Helper"),
            (report_id, "Electrical", "Electrical Type", "Zone B", 5, "3 Electrician, 2 Helper"),
        ]
        db_manager.executemany(
            "INSERT INTO report_items (report_id, contractor, type, zone, workers, details) VALUES (?, ?, ?, ?, ?, ?)",
            items,
        )
        db_manager.commit()

        cursor = db_manager.execute(
            "SELECT COUNT(*) as cnt FROM report_items WHERE report_id = ?", (report_id,)
        )
        cnt = cursor.fetchone()["cnt"]
        assert cnt == 2, f"Expected 2 items, got {cnt}"

    def test_commit_and_rollback(self, db_manager: DatabaseManager):
        """Should support commit and rollback."""
        db_manager.execute(
            "INSERT INTO reports (date, day, status, created_at) VALUES (?, ?, ?, ?)",
            ("2026-07-11", "السبت", "final", "2026-07-11T10:00:00"),
        )
        db_manager.rollback()

        cursor = db_manager.execute("SELECT COUNT(*) as cnt FROM reports")
        cnt = cursor.fetchone()["cnt"]
        assert cnt == 0, f"Expected 0 reports after rollback, got {cnt}"

    def test_thread_local_connections(self, db_manager: DatabaseManager):
        """Should provide thread-local connections."""
        conn1 = db_manager.connection()
        conn2 = db_manager.connection()
        assert conn1 is conn2, "Same thread should return the same connection"

    def test_close(self, db_manager: DatabaseManager):
        """Should close connection without error."""
        db_manager.execute("SELECT 1")
        db_manager.close()

    def test_table_exists(self, db_manager: DatabaseManager):
        """Should check table existence."""
        assert db_manager.table_exists("reports"), "reports table should exist"
        assert not db_manager.table_exists("nonexistent_table"), "nonexistent_table should not exist"

    def test_db_path_property(self, db_path: Path, db_manager: DatabaseManager):
        """Should return the correct database path."""
        assert db_manager.db_path == db_path.resolve(), f"Expected {db_path.resolve()}, got {db_manager.db_path}"

    def test_error_on_invalid_sql(self, db_manager: DatabaseManager):
        """Should raise DatabaseError on invalid SQL."""
        with pytest.raises(DatabaseError):
            db_manager.execute("INVALID SQL STATEMENT")

    def test_double_init_does_not_break(self, db_path: Path):
        """Should handle re-initialization gracefully."""
        manager1 = DatabaseManager(str(db_path))
        manager1.close_all()
        manager2 = DatabaseManager(str(db_path))
        assert manager2.table_exists("reports"), "reports table should exist after re-init"
        manager2.close_all()

    def test_execute_with_no_params(self, db_manager: DatabaseManager):
        """Should execute SQL without parameters."""
        result = db_manager.execute("SELECT 1 AS val").fetchone()
        assert result is not None, "Should return a row"
        assert result["val"] == 1, f"Expected 1, got {result['val']}"

    def test_execute_with_wrong_param_count(self, db_manager: DatabaseManager):
        """Should raise DatabaseError for wrong parameter count."""
        with pytest.raises(DatabaseError):
            db_manager.execute("SELECT ?", (1, 2))

    def test_close_before_any_operation(self):
        """Should not raise when closing before any operation."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = DatabaseManager(str(Path(tmp) / "fresh.db"))
            db.close()
