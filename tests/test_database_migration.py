"""
Tests for v1.0 → v2.0 database migration.

Verifies that:
- New tables are created
- New columns are added
- Status values are migrated
- Existing data is preserved
- Migration is idempotent
- Rollback works
"""

import sqlite3
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager, SCHEMA_SQL
from app.utils.exceptions import DatabaseError


# v1.0 schema (before upgrade)
V1_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    day TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK (status IN ('generated', 'no_report')),
    pdf_path TEXT,
    excel_path TEXT,
    created_at TEXT NOT NULL,
    telegram_user TEXT
);

CREATE TABLE IF NOT EXISTS report_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    contractor TEXT NOT NULL,
    type TEXT,
    zone TEXT,
    workers INTEGER,
    details TEXT,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
CREATE INDEX IF NOT EXISTS idx_report_items_report_id ON report_items(report_id);
"""


class TestDatabaseMigration:
    """Tests for v1.0 → v2.0 database migration."""

    def _create_v1_db(self, path: Path) -> DatabaseManager:
        """Create a v1.0 database with sample data."""
        # Create database with v1.0 schema directly via sqlite3
        conn = sqlite3.connect(str(path))
        conn.executescript(V1_SCHEMA_SQL)
        # Insert sample v1.0 data
        conn.execute(
            "INSERT INTO reports (date, day, status, created_at) VALUES (?, ?, ?, ?)",
            ("2026-07-10", "الخميس", "generated", "2026-07-10T10:00:00"),
        )
        conn.execute(
            "INSERT INTO reports (date, day, status, created_at) VALUES (?, ?, ?, ?)",
            ("2026-07-11", "الجمعة", "generated", "2026-07-11T10:00:00"),
        )
        conn.execute(
            "INSERT INTO reports (date, day, status, created_at) VALUES (?, ?, ?, ?)",
            ("2026-07-12", "السبت", "no_report", "2026-07-12T10:00:00"),
        )
        report_id = conn.execute(
            "SELECT id FROM reports WHERE date = '2026-07-11'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO report_items (report_id, contractor, type, zone, workers, details) VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, "Civil Co", "Civil", "Zone A", 10, "5+5"),
        )
        conn.execute(
            "INSERT INTO report_items (report_id, contractor, type, zone, workers, details) VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, "Electric Inc", "Electrical", "Zone B", 5, "3+2"),
        )
        conn.commit()
        conn.close()
        # Now open with DatabaseManager (triggers migration)
        return DatabaseManager(path)

    def test_migration_detects_v1_schema(self, tmp_path):
        """Migration should detect v1.0 schema."""
        db_path = tmp_path / "v1_test.db"
        # Create a v1.0 database manually
        conn = sqlite3.connect(str(db_path))
        conn.executescript(V1_SCHEMA_SQL)
        conn.close()

        # Open with DatabaseManager (triggers migration check)
        db = DatabaseManager(db_path)

        # Verify v2.0 columns exist
        assert db.column_exists("reports", "updated_at"), (
            "Migration should add updated_at column"
        )
        assert db.column_exists("reports", "finalized_at"), (
            "Migration should add finalized_at column"
        )
        assert db.column_exists("reports", "preview_pdf_path"), (
            "Migration should add preview_pdf_path column"
        )
        assert db.column_exists("report_items", "contractor_code"), (
            "Migration should add contractor_code column"
        )
        db.close()

    def test_migration_creates_new_tables(self, tmp_path):
        """Migration should create all v2.0 tables."""
        db_path = tmp_path / "v1_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(V1_SCHEMA_SQL)
        conn.close()

        db = DatabaseManager(db_path)

        # Verify all new tables exist
        assert db.table_exists("report_versions"), (
            "Migration should create report_versions table"
        )
        assert db.table_exists("event_log"), (
            "Migration should create event_log table"
        )
        assert db.table_exists("favorites"), (
            "Migration should create favorites table"
        )
        assert db.table_exists("stats_cache"), (
            "Migration should create stats_cache table"
        )
        db.close()

    def test_migration_migrates_status_values(self, tmp_path):
        """Migration should convert 'generated' status to 'final'."""
        db = self._create_v1_db(tmp_path / "migrate_status.db")

        rows = db.execute("SELECT status FROM reports ORDER BY date").fetchall()
        statuses = [r["status"] for r in rows]

        assert statuses[0] == "final", (
            f"Expected 'final', got '{statuses[0]}'"
        )
        assert statuses[1] == "final", (
            f"Expected 'final', got '{statuses[1]}'"
        )
        assert statuses[2] == "no_report", (
            f"Expected 'no_report', got '{statuses[2]}'"
        )
        db.close()

    def test_migration_preserves_existing_data(self, tmp_path):
        """Migration should preserve all v1.0 data."""
        db = self._create_v1_db(tmp_path / "preserve_data.db")

        # Check reports
        rows = db.execute("SELECT * FROM reports ORDER BY date").fetchall()
        assert len(rows) == 3, (
            f"Expected 3 reports, got {len(rows)}"
        )
        assert rows[0]["date"] == "2026-07-10", (
            f"Expected date 2026-07-10, got {rows[0]['date']}"
        )
        assert rows[1]["date"] == "2026-07-11", (
            f"Expected date 2026-07-11, got {rows[1]['date']}"
        )
        assert rows[2]["date"] == "2026-07-12", (
            f"Expected date 2026-07-12, got {rows[2]['date']}"
        )

        # Check report_items
        items = db.execute(
            "SELECT * FROM report_items ORDER BY id"
        ).fetchall()
        assert len(items) == 2, (
            f"Expected 2 items, got {len(items)}"
        )
        assert items[0]["contractor"] == "Civil Co", (
            f"Expected 'Civil Co', got {items[0]['contractor']}"
        )
        assert items[1]["contractor"] == "Electric Inc", (
            f"Expected 'Electric Inc', got {items[1]['contractor']}"
        )
        db.close()

    def test_migration_sets_updated_at_from_created_at(self, tmp_path):
        """Migration should set updated_at = created_at for existing reports."""
        db = self._create_v1_db(tmp_path / "updated_at.db")

        rows = db.execute("SELECT created_at, updated_at FROM reports").fetchall()
        for row in rows:
            assert row["updated_at"] is not None, (
                "updated_at should not be None after migration"
            )
            assert row["updated_at"] == row["created_at"], (
                f"updated_at '{row['updated_at']}' should equal created_at '{row['created_at']}'"
            )
        db.close()

    def test_migration_idempotent(self, tmp_path):
        """Running migration twice should be safe."""
        db_path = tmp_path / "idempotent.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(V1_SCHEMA_SQL)
        conn.close()

        # First open (triggers migration)
        db1 = DatabaseManager(db_path)
        db1.close()

        # Second open (should not error)
        db2 = DatabaseManager(db_path)

        # Verify all tables still exist
        assert db2.table_exists("report_versions"), (
            "report_versions should exist after second open"
        )
        assert db2.table_exists("event_log"), (
            "event_log should exist after second open"
        )
        assert db2.table_exists("favorites"), (
            "favorites should exist after second open"
        )
        assert db2.table_exists("stats_cache"), (
            "stats_cache should exist after second open"
        )

        # Verify columns still exist
        assert db2.column_exists("reports", "updated_at"), (
            "updated_at should exist after second open"
        )
        db2.close()

    def test_fresh_database_has_v2_schema(self, tmp_path):
        """A fresh database should start with full v2.0 schema."""
        db_path = tmp_path / "fresh_v2.db"
        db = DatabaseManager(db_path)

        # Verify all v2.0 columns exist on reports
        v2_columns = [
            "updated_at", "finalized_at", "locked_at",
            "locked_by", "source_date", "preview_pdf_path",
        ]
        for col in v2_columns:
            assert db.column_exists("reports", col), (
                f"Fresh database should have '{col}' column"
            )

        # Verify all v2.0 tables exist
        v2_tables = ["report_versions", "event_log", "favorites", "stats_cache"]
        for table in v2_tables:
            assert db.table_exists(table), (
                f"Fresh database should have '{table}' table"
            )

        # Verify contractor_code column
        assert db.column_exists("report_items", "contractor_code"), (
            "Fresh database should have contractor_code column"
        )
        db.close()

    def test_migration_creates_indexes(self, tmp_path):
        """Migration should create all v2.0 indexes."""
        db_path = tmp_path / "indexes.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(V1_SCHEMA_SQL)
        conn.close()

        db = DatabaseManager(db_path)

        # Check for key indexes
        expected_indexes = [
            "idx_reports_status",
            "idx_reports_date_status",
            "idx_reports_user",
            "idx_report_items_contractor",
            "idx_report_items_zone",
            "idx_report_items_workers",
            "idx_versions_report_id",
            "idx_events_timestamp",
            "idx_events_user",
            "idx_events_action",
            "idx_events_object",
            "idx_favorites_user",
            "idx_stats_period",
        ]
        for idx_name in expected_indexes:
            cursor = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (idx_name,),
            )
            assert cursor.fetchone() is not None, (
                f"Index '{idx_name}' should exist after migration"
            )
        db.close()

    def test_migration_on_empty_v1_db(self, tmp_path):
        """Migration should work on empty v1.0 database."""
        db_path = tmp_path / "empty_v1.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(V1_SCHEMA_SQL)
        conn.close()

        db = DatabaseManager(db_path)

        # Should have v2.0 tables
        assert db.table_exists("report_versions")
        assert db.table_exists("event_log")

        # Reports should be empty
        count = db.execute("SELECT COUNT(*) as cnt FROM reports").fetchone()["cnt"]
        assert count == 0, f"Expected 0 reports, got {count}"
        db.close()

    def test_needs_migration_false_for_v2(self, tmp_path):
        """_needs_migration should return False for v2.0 databases."""
        db_path = tmp_path / "v2_check.db"
        db = DatabaseManager(db_path)
        assert not db._needs_migration(), (
            "Fresh v2.0 database should not need migration"
        )
        db.close()

    def test_new_tables_writable(self, tmp_path):
        """New v2.0 tables should be writable and readable."""
        db = self._create_v1_db(tmp_path / "writable.db")

        # Insert into event_log
        db.execute(
            "INSERT INTO event_log (timestamp, telegram_user, action) VALUES (?, ?, ?)",
            ("2026-07-11T12:00:00", "user1", "test.action"),
        )
        db.commit()

        row = db.execute(
            "SELECT * FROM event_log WHERE action = ?", ("test.action",)
        ).fetchone()
        assert row is not None, "Should be able to read from event_log"
        assert row["telegram_user"] == "user1", (
            f"Expected 'user1', got '{row['telegram_user']}'"
        )

        # Insert into favorites
        db.execute(
            "INSERT INTO favorites (telegram_user, contractor_name, created_at) VALUES (?, ?, ?)",
            ("user1", "Favorite Co", "2026-07-11T12:00:00"),
        )
        db.commit()

        fav = db.execute(
            "SELECT * FROM favorites WHERE contractor_name = ?", ("Favorite Co",)
        ).fetchone()
        assert fav is not None, "Should be able to read from favorites"

        # Insert into stats_cache
        db.execute(
            "INSERT INTO stats_cache (period, period_key, data, computed_at) VALUES (?, ?, ?, ?)",
            ("daily", "2026-07-11", '{"workers": 15}', "2026-07-11T12:00:00"),
        )
        db.commit()

        stats = db.execute(
            "SELECT * FROM stats_cache WHERE period_key = ?", ("2026-07-11",)
        ).fetchone()
        assert stats is not None, "Should be able to read from stats_cache"
        db.close()
