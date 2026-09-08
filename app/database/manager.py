"""
Database connection manager for Labor-Report.

Handles SQLite connection lifecycle, schema initialization,
and provides a context manager for safe database operations.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Schema definition for the reports database (v2.0)
SCHEMA_SQL = """
-- Core reports table (v2.0: extended lifecycle fields)
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    day TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'final', 'locked', 'no_report')),
    pdf_path TEXT,
    excel_path TEXT,
    preview_pdf_path TEXT,
    telegram_user TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    finalized_at TEXT,
    locked_at TEXT,
    locked_by TEXT,
    source_date TEXT
);

-- Report items (contractor rows)
CREATE TABLE IF NOT EXISTS report_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    contractor TEXT NOT NULL,
    type TEXT,
    zone TEXT,
    workers INTEGER,
    details TEXT,
    contractor_code TEXT,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

-- Version history (v2.0)
CREATE TABLE IF NOT EXISTS report_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    change_summary TEXT,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    UNIQUE(report_id, version_number)
);

-- Event log / audit trail (v2.0)
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    telegram_user TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT,
    object_id INTEGER,
    object_date TEXT,
    old_value TEXT,
    new_value TEXT
);

-- Per-user favorite contractors (v2.0)
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user TEXT NOT NULL,
    contractor_name TEXT NOT NULL,
    contractor_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(telegram_user, contractor_name)
);

-- Pre-computed statistics cache (v2.0)
CREATE TABLE IF NOT EXISTS stats_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    data TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    UNIQUE(period, period_key)
);

-- Indexes for reports
CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_date_status ON reports(date, status);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(telegram_user);

-- Indexes for report_items
CREATE INDEX IF NOT EXISTS idx_report_items_report_id ON report_items(report_id);
CREATE INDEX IF NOT EXISTS idx_report_items_contractor ON report_items(contractor);
CREATE INDEX IF NOT EXISTS idx_report_items_type ON report_items(type);
CREATE INDEX IF NOT EXISTS idx_report_items_zone ON report_items(zone);
CREATE INDEX IF NOT EXISTS idx_report_items_workers ON report_items(workers);

-- Indexes for versions
CREATE INDEX IF NOT EXISTS idx_versions_report_id ON report_versions(report_id);

-- Indexes for event_log
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON event_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_user ON event_log(telegram_user);
CREATE INDEX IF NOT EXISTS idx_events_action ON event_log(action);
CREATE INDEX IF NOT EXISTS idx_events_object ON event_log(object_type, object_id);

-- Indexes for favorites
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(telegram_user);

-- Indexes for stats_cache
CREATE INDEX IF NOT EXISTS idx_stats_period ON stats_cache(period, period_key);

-- Users / roles table (v3.0)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'pending'
        CHECK (role IN ('superadmin', 'project_manager', 'executive_engineer', 'admin', 'normal_user', 'viewer', 'pending', 'rejected')),
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_by TEXT,
    approved_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- User-added contractors (mventor-ticket-036)
CREATE TABLE IF NOT EXISTS contractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    type TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_contractors_name ON contractors(name);
"""


class DatabaseManager:
    """Manages SQLite database connection and schema.

    Thread-safe connection handling with automatic schema initialization.
    Designed for single-user/small-team usage with SQLite.

    Usage:
        db = DatabaseManager("database/labor_reports.db")
        with db.connection() as conn:
            cursor = conn.execute("SELECT * FROM reports")
            ...
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the database manager.

        Args:
            db_path: Path to the SQLite database file.

        Raises:
            DatabaseError: If the database directory cannot be created
                          or schema initialization fails.
        """
        self._db_path = Path(db_path).resolve()
        self._local = threading.local()
        self._lock = threading.Lock()

        # Ensure parent directory exists
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DatabaseError(
                f"Cannot create database directory: {self._db_path.parent}",
                original_exception=e,
            ) from e

        # Initialize schema on startup (creates new tables/indexes)
        self._init_schema()

        # Run v1.0 → v2.0 migration if needed (adds columns, migrates data)
        self.run_migration()

        # Widen users.role CHECK to include extended roles on existing DBs
        # (CREATE TABLE IF NOT EXISTS never alters an existing table).
        self._ensure_extended_roles()

        logger.info("Database initialized: %s", self._db_path)

    @property
    def db_path(self) -> Path:
        """Get the resolved database file path."""
        return self._db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local database connection.

        Creates a new connection if one doesn't exist for this thread.

        Returns:
            An active SQLite connection.
        """
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.connection = conn
        return self._local.connection

    def connection(self) -> sqlite3.Connection:
        """Get a database connection for the current thread.

        Returns:
            An active SQLite connection with WAL mode and foreign keys enabled.
        """
        return self._get_connection()

    def _init_schema(self) -> None:
        """Initialize the database schema.

        Creates tables and indexes if they don't exist.

        Raises:
            DatabaseError: If schema initialization fails.
        """
        try:
            conn = self._get_connection()
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            logger.debug("Database schema initialized successfully.")
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to initialize database schema: {e}",
                original_exception=e,
            ) from e

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement.

        Args:
            sql: SQL statement to execute.
            params: Parameters for the SQL statement.

        Returns:
            The cursor after execution.

        Raises:
            DatabaseError: If execution fails.
        """
        try:
            conn = self._get_connection()
            return conn.execute(sql, params)
        except sqlite3.Error as e:
            raise DatabaseError(f"Database execute error: {e}", original_exception=e) from e

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """Execute a SQL statement multiple times with different parameters.

        Args:
            sql: SQL statement to execute.
            params_list: List of parameter tuples.

        Returns:
            The cursor after execution.

        Raises:
            DatabaseError: If execution fails.
        """
        try:
            conn = self._get_connection()
            return conn.executemany(sql, params_list)
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Database executemany error: {e}", original_exception=e
            ) from e

    def commit(self) -> None:
        """Commit the current transaction.

        Raises:
            DatabaseError: If commit fails.
        """
        try:
            self._get_connection().commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Database commit error: {e}", original_exception=e) from e

    def rollback(self) -> None:
        """Roll back the current transaction.

        Raises:
            DatabaseError: If rollback fails.
        """
        try:
            self._get_connection().rollback()
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Database rollback error: {e}", original_exception=e
            ) from e

    def close(self) -> None:
        """Close the database connection for the current thread.

        Performs a WAL checkpoint and switches to DELETE journal mode
        before closing to ensure the WAL and SHM files are cleaned up,
        preventing PermissionErrors on Windows during temp directory cleanup.
        """
        if hasattr(self._local, "connection") and self._local.connection is not None:
            try:
                # Checkpoint WAL and switch to DELETE mode so -wal and -shm
                # files are removed when the connection closes.
                self._local.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._local.connection.execute("PRAGMA journal_mode=DELETE")
                self._local.connection.commit()
            except sqlite3.Error:
                pass  # Best-effort cleanup
            try:
                self._local.connection.close()
            except sqlite3.Error as e:
                logger.warning("Error closing database connection: %s", e)
            finally:
                self._local.connection = None

    def close_all(self) -> None:
        """Close all database connections (thread-safe).

        Should be called during application shutdown.
        """
        # The thread-local connections will be garbage collected
        # when the thread ends. This is a best-effort cleanup.
        self.close()

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database.

        Args:
            table_name: Name of the table to check.

        Returns:
            True if the table exists.
        """
        cursor = self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return cursor.fetchone() is not None

    def column_exists(self, table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table.

        Args:
            table_name: Name of the table.
            column_name: Name of the column to check.

        Returns:
            True if the column exists.
        """
        cursor = self.execute(f"PRAGMA table_info({table_name})")
        columns = [row["name"] for row in cursor.fetchall()]
        return column_name in columns

    # --- v2.0 Migration ---

    def run_migration(self) -> bool:
        """Run v1.0 to v2.0 database migration if needed.

        Checks if the database needs migration (v1.0 schema detected)
        and runs the migration to add all v2.0 tables, columns, and indexes.

        The migration is idempotent â€” safe to run multiple times.

        Returns:
            True if migration was performed, False if already up-to-date.

        Raises:
            DatabaseError: If migration fails.
        """
        if not self._needs_migration():
            logger.info("Database schema is already v2.0 â€” no migration needed.")
            return False

        logger.info("v1.0 database detected â€” starting migration to v2.0...")
        self._migrate_v1_to_v2()
        logger.info("Database migration to v2.0 completed successfully.")
        return True

    def _needs_migration(self) -> bool:
        """Check if the database needs migration from v1.0 to v2.0.

        Detects v1.0 schema by checking for the absence of v2.0 columns
        (e.g., 'updated_at' in the reports table).

        Returns:
            True if migration is needed.
        """
        if not self.table_exists("reports"):
            return False  # Fresh database, schema init handles it
        # Check for v2.0 column â€” if absent, it's a v1.0 database
        return not self.column_exists("reports", "updated_at")

    def _migrate_v1_to_v2(self) -> None:
        """Perform the v1.0 to v2.0 schema migration.

        Adds new columns to existing tables, creates new tables,
        adds indexes, and migrates status values.

        Uses ALTER TABLE for new columns (SQLite supports ADD COLUMN).
        Uses CREATE TABLE IF NOT EXISTS for new tables.
        All operations are wrapped in a transaction for atomicity.
        """
        try:
            conn = self._get_connection()

            # Step 1: Add new columns to reports table
            v2_report_columns = [
                ("preview_pdf_path", "TEXT"),
                ("updated_at", "TEXT"),
                ("finalized_at", "TEXT"),
                ("locked_at", "TEXT"),
                ("locked_by", "TEXT"),
                ("source_date", "TEXT"),
            ]
            for col_name, col_type in v2_report_columns:
                if not self.column_exists("reports", col_name):
                    conn.execute(
                        f"ALTER TABLE reports ADD COLUMN {col_name} {col_type}"
                    )
                    logger.debug("Added column 'reports.%s'", col_name)

            # Step 2: Add new column to report_items
            if not self.column_exists("report_items", "contractor_code"):
                conn.execute("ALTER TABLE report_items ADD COLUMN contractor_code TEXT")
                logger.debug("Added column 'report_items.contractor_code'")

            # Step 3: Migrate status values (generated â†’ final)
            # The v1 schema has CHECK (status IN ('generated', 'no_report')),
            # so temporarily ignore CHECK constraints to allow the update.
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute(
                "UPDATE reports SET status = 'final' WHERE status = 'generated'"
            )
            conn.execute("PRAGMA ignore_check_constraints = OFF")
            migrated = conn.execute(
                "SELECT changes()"
            ).fetchone()[0]
            if migrated:
                logger.info("Migrated %d report status(es) from 'generated' to 'final'", migrated)

            # Step 4: Set updated_at for existing reports
            conn.execute(
                "UPDATE reports SET updated_at = created_at WHERE updated_at IS NULL"
            )
            logger.debug("Set updated_at for existing reports")

            # Step 5: Reinitialize schema (creates new tables and indexes)
            conn.executescript(SCHEMA_SQL)

            conn.commit()
            logger.info("v1.0 â†’ v2.0 migration complete.")

        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(
                f"Database migration failed: {e}",
                original_exception=e,
            ) from e

    def _ensure_extended_roles(self) -> bool:
        """Widen the users.role CHECK to include extended roles.

        Covers 'project_manager' and 'executive_engineer'. SQLite cannot
        ALTER a CHECK constraint, so an existing users table is rebuilt
        (rename → create → copy → drop). Idempotent: no-op when the
        CHECK already allows all extended roles.

        Returns:
            True if the table was rebuilt, False if already up-to-date.
        """
        row = self.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        ddl = row["sql"] or "" if row else ""
        if row is None or ("project_manager" in ddl and "executive_engineer" in ddl):
            return False
        try:
            conn = self._get_connection()
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("ALTER TABLE users RENAME TO users_old")
            conn.execute(
                """CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'pending'
                        CHECK (role IN ('superadmin', 'project_manager', 'executive_engineer', 'admin', 'normal_user', 'viewer', 'pending', 'rejected')),
                    username TEXT,
                    first_name TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    approved_by TEXT,
                    approved_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            conn.execute(
                """INSERT INTO users
                    (id, chat_id, role, username, first_name, created_at,
                     approved_by, approved_at, updated_at)
                   SELECT id, chat_id, role, username, first_name, created_at,
                     approved_by, approved_at, updated_at FROM users_old"""
            )
            conn.execute("DROP TABLE users_old")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            logger.info("users.role CHECK widened to include extended roles.")
            return True
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(
                f"Failed to add extended roles: {e}",
                original_exception=e,
            ) from e
