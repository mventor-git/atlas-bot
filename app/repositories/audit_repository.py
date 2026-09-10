"""
Audit Repository for Labor-Report.

Manages the user_activity_log table which tracks:
- What values users added to reports
- What values were reverted
- Who viewed reports
- Who exported PDFs
"""

from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.models.audit import UserActivityLog
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AuditRepository:
    """Repository for user activity audit logging."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager
        self._ensure_table()

    ALLOWED_ACTIONS = frozenset({"added", "reverted", "viewed", "exported", "locked", "unlocked"})

    def _ensure_table(self) -> None:
        """Create the user_activity_log table if it doesn't exist.
        
        For existing tables, migrates the CHECK constraint to allow
        'locked' and 'unlocked' actions (G1 fix).
        """
        try:
            if self._db.table_exists("user_activity_log") is False:
                # Create table — no CHECK constraint (validated in app layer)
                self._db.execute("""CREATE TABLE IF NOT EXISTS user_activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user TEXT NOT NULL,
                    site_id TEXT NOT NULL DEFAULT 'default',
                    user_role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    report_date TEXT,
                    report_status TEXT,
                    contractor_name TEXT,
                    workers INTEGER,
                    zone TEXT,
                    details TEXT,
                    reverted_entry_id INTEGER,
                    timestamp TEXT NOT NULL
                )""")
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON user_activity_log(telegram_user)")
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_action ON user_activity_log(action)")
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_date ON user_activity_log(report_date)")
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON user_activity_log(timestamp DESC)")
                self._db.commit()
                logger.info("Created user_activity_log table with indexes")
                return

            # Table exists — check if old CHECK constraint restricts 'locked'/'unlocked'
            # by attempting a dummy insert (rolled back)
            try:
                self._db.execute("SAVEPOINT check_constraint")
                self._db.execute(
                    """INSERT INTO user_activity_log
                       (telegram_user, user_role, action, timestamp)
                       VALUES ('_migrate_check', '_migrate_check', 'locked', '2000-01-01T00:00:00')"""
                )
                self._db.execute("ROLLBACK TO SAVEPOINT check_constraint")
                # No error — constraint already allows 'locked'
            except Exception:
                # Old CHECK constraint blocks 'locked' — drop and recreate table
                self._db.execute("ROLLBACK TO SAVEPOINT check_constraint")
                logger.info("Migrating user_activity_log CHECK constraint to allow 'locked'/'unlocked'")
                self._db.execute("""CREATE TABLE user_activity_log_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user TEXT NOT NULL,
                    site_id TEXT NOT NULL DEFAULT 'default',
                    user_role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    report_date TEXT,
                    report_status TEXT,
                    contractor_name TEXT,
                    workers INTEGER,
                    zone TEXT,
                    details TEXT,
                    reverted_entry_id INTEGER,
                    timestamp TEXT NOT NULL
                )""")
                self._db.execute(
                    """INSERT INTO user_activity_log_v2
                    (id, telegram_user, user_role, action, report_date,
                     report_status, contractor_name, workers, zone, details,
                     reverted_entry_id, timestamp)
                    SELECT id, telegram_user, user_role, action, report_date,
                     report_status, contractor_name, workers, zone, details,
                     reverted_entry_id, timestamp FROM user_activity_log"""
                )
                self._db.execute("DROP TABLE user_activity_log")
                self._db.execute("ALTER TABLE user_activity_log_v2 RENAME TO user_activity_log")
                self._db.ensure_site_columns()
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON user_activity_log(telegram_user)")
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_action ON user_activity_log(action)")
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_date ON user_activity_log(report_date)")
                self._db.execute("CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON user_activity_log(timestamp DESC)")
                self._db.commit()
                logger.info("user_activity_log table migrated — CHECK constraint removed")
        except Exception as e:
            logger.warning("Could not create/migrate user_activity_log table: %s", e)

    # --- Logging ---

    def log_activity(self, entry: UserActivityLog) -> UserActivityLog:
        """Log a user activity entry.

        Args:
            entry: The UserActivityLog dataclass.

        Returns:
            The entry with database ID populated.
        """
        cursor = self._db.execute(
            """INSERT INTO user_activity_log
                (telegram_user, user_role, action, report_date, report_status,
                 contractor_name, workers, zone, details, reverted_entry_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.telegram_user,
                entry.user_role,
                entry.action,
                entry.report_date,
                entry.report_status,
                entry.contractor_name,
                entry.workers,
                entry.zone,
                entry.details,
                entry.reverted_entry_id,
                entry.timestamp,
            ),
        )
        self._db.commit()
        entry.id = cursor.lastrowid
        logger.debug("Logged activity: %s by %s (%s)", entry.action, entry.telegram_user, entry.user_role)
        return entry

    def log_added(
        self,
        telegram_user: str,
        user_role: str,
        report_date: str,
        report_status: str,
        contractor_name: str,
        workers: Optional[int] = None,
        zone: Optional[str] = None,
        details: Optional[str] = None,
    ) -> UserActivityLog:
        """Log a contractor value being added to a report.

        Args:
            telegram_user: User who added the value.
            user_role: User's role at the time.
            report_date: Report date.
            report_status: Report status.
            contractor_name: Contractor name.
            workers: Number of workers.
            zone: Work zone.
            details: Work details.

        Returns:
            The logged entry (has .id for revert reference).
        """
        entry = UserActivityLog(
            telegram_user=telegram_user,
            user_role=user_role,
            action="added",
            report_date=report_date,
            report_status=report_status,
            contractor_name=contractor_name,
            workers=workers,
            zone=zone,
            details=details,
        )
        return self.log_activity(entry)

    def log_reverted(
        self,
        telegram_user: str,
        user_role: str,
        report_date: str,
        report_status: str,
        original_entry_id: int,
        contractor_name: Optional[str] = None,
    ) -> UserActivityLog:
        """Log a value being reverted.

        Args:
            telegram_user: User who performed the revert.
            user_role: User's role at the time.
            report_date: Report date.
            report_status: Report status.
            original_entry_id: ID of the original 'added' entry being reverted.
            contractor_name: Contractor name (optional).

        Returns:
            The logged entry.
        """
        entry = UserActivityLog(
            telegram_user=telegram_user,
            user_role=user_role,
            action="reverted",
            report_date=report_date,
            report_status=report_status,
            contractor_name=contractor_name,
            reverted_entry_id=original_entry_id,
        )
        return self.log_activity(entry)

    def log_viewed(
        self,
        telegram_user: str,
        user_role: str,
        report_date: str,
        report_status: str,
    ) -> UserActivityLog:
        """Log a user viewing a report.

        Args:
            telegram_user: User who viewed.
            user_role: User's role at the time.
            report_date: Report date.
            report_status: Report status.

        Returns:
            The logged entry.
        """
        entry = UserActivityLog(
            telegram_user=telegram_user,
            user_role=user_role,
            action="viewed",
            report_date=report_date,
            report_status=report_status,
        )
        return self.log_activity(entry)

    def log_locked(
        self,
        telegram_user: str,
        user_role: str,
        report_date: str,
        report_status: str,
        details: Optional[str] = None,
    ) -> UserActivityLog:
        """Log a report being locked.

        Args:
            telegram_user: User who locked the report.
            user_role: User's role at the time.
            report_date: Report date.
            report_status: Report status at lock time.
            details: Optional details about the lock (e.g. 'auto-lock').

        Returns:
            The logged entry.
        """
        entry = UserActivityLog(
            telegram_user=telegram_user,
            user_role=user_role,
            action="locked",
            report_date=report_date,
            report_status=report_status,
            details=details,
        )
        return self.log_activity(entry)

    def log_unlocked(
        self,
        telegram_user: str,
        user_role: str,
        report_date: str,
        report_status: str,
        details: Optional[str] = None,
    ) -> UserActivityLog:
        """Log a report being unlocked.

        Args:
            telegram_user: User who unlocked the report.
            user_role: User's role at the time.
            report_date: Report date.
            report_status: Report status at unlock time.
            details: Optional details about the unlock.

        Returns:
            The logged entry.
        """
        entry = UserActivityLog(
            telegram_user=telegram_user,
            user_role=user_role,
            action="unlocked",
            report_date=report_date,
            report_status=report_status,
            details=details,
        )
        return self.log_activity(entry)

    def log_exported(
        self,
        telegram_user: str,
        user_role: str,
        report_date: str,
        report_status: str,
    ) -> UserActivityLog:
        """Log a user exporting/downloading a PDF.

        Args:
            telegram_user: User who exported.
            user_role: User's role at the time.
            report_date: Report date.
            report_status: Report status.

        Returns:
            The logged entry.
        """
        entry = UserActivityLog(
            telegram_user=telegram_user,
            user_role=user_role,
            action="exported",
            report_date=report_date,
            report_status=report_status,
        )
        return self.log_activity(entry)

    # --- Queries ---

    def get_by_user(self, telegram_user: str, limit: int = 100) -> list[UserActivityLog]:
        """Get all activity for a specific user.

        Args:
            telegram_user: Telegram user ID.
            limit: Maximum entries to return.

        Returns:
            List of UserActivityLog entries, newest first.
        """
        cursor = self._db.execute(
            "SELECT * FROM user_activity_log WHERE telegram_user = ? ORDER BY timestamp DESC LIMIT ?",
            (telegram_user, limit),
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_by_action(self, action: str, limit: int = 100) -> list[UserActivityLog]:
        """Get all entries with a specific action.

        Args:
            action: 'added', 'reverted', 'viewed', 'exported'.
            limit: Maximum entries to return.

        Returns:
            List of UserActivityLog entries.
        """
        cursor = self._db.execute(
            "SELECT * FROM user_activity_log WHERE action = ? ORDER BY timestamp DESC LIMIT ?",
            (action, limit),
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_locked_reports(self, limit: int = 100) -> list[UserActivityLog]:
        """Get all lock events.

        Args:
            limit: Maximum entries to return.

        Returns:
            List of 'locked' UserActivityLog entries.
        """
        return self.get_by_action("locked", limit)

    def get_unlocked_reports(self, limit: int = 100) -> list[UserActivityLog]:
        """Get all unlock events.

        Args:
            limit: Maximum entries to return.

        Returns:
            List of 'unlocked' UserActivityLog entries.
        """
        return self.get_by_action("unlocked", limit)

    def get_added_values_by_user(self, telegram_user: str) -> list[UserActivityLog]:
        """Get all 'added' entries by a specific user (non-reverted).

        Args:
            telegram_user: Telegram user ID.

        Returns:
            List of 'added' UserActivityLog entries that were NOT reverted.
        """
        cursor = self._db.execute(
            """SELECT * FROM user_activity_log
            WHERE telegram_user = ? AND action = 'added'
            AND id NOT IN (
                SELECT reverted_entry_id FROM user_activity_log
                WHERE action = 'reverted' AND reverted_entry_id IS NOT NULL
            )
            ORDER BY timestamp DESC""",
            (telegram_user,),
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_all_added_values(self) -> list[UserActivityLog]:
        """Get all 'added' entries across all users (non-reverted).

        Returns:
            List of 'added' UserActivityLog entries that were NOT reverted.
        """
        cursor = self._db.execute(
            """SELECT * FROM user_activity_log
            WHERE action = 'added'
            AND id NOT IN (
                SELECT reverted_entry_id FROM user_activity_log
                WHERE action = 'reverted' AND reverted_entry_id IS NOT NULL
            )
            ORDER BY timestamp DESC"""
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_reverted_by_user(self, telegram_user: str) -> list[UserActivityLog]:
        """Get all 'reverted' entries by a specific user.

        Args:
            telegram_user: Telegram user ID.

        Returns:
            List of 'reverted' UserActivityLog entries.
        """
        cursor = self._db.execute(
            "SELECT * FROM user_activity_log WHERE telegram_user = ? AND action = 'reverted' ORDER BY timestamp DESC",
            (telegram_user,),
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_views_by_user(self, telegram_user: str) -> list[UserActivityLog]:
        """Get all 'viewed' entries by a specific user.

        Args:
            telegram_user: Telegram user ID.

        Returns:
            List of 'viewed' UserActivityLog entries.
        """
        cursor = self._db.execute(
            "SELECT * FROM user_activity_log WHERE telegram_user = ? AND action = 'viewed' ORDER BY timestamp DESC",
            (telegram_user,),
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_recent_by_all_users(self, limit: int = 50) -> list[UserActivityLog]:
        """Get the most recent activity across all users.

        Args:
            limit: Maximum entries to return.

        Returns:
            List of recent UserActivityLog entries.
        """
        cursor = self._db.execute(
            "SELECT * FROM user_activity_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_distinct_users(self) -> list[dict]:
        """Get distinct users who have activity logs with their roles.

        Returns:
            List of dicts with 'telegram_user' and 'user_role' keys.
        """
        cursor = self._db.execute(
            """SELECT telegram_user, user_role, MAX(timestamp) as last_active
            FROM user_activity_log
            GROUP BY telegram_user
            ORDER BY last_active DESC"""
        )
        return [dict(row) for row in cursor.fetchall()]

    # --- Helpers ---

    @staticmethod
    def _safe_get(row, key: str):
        """Safely get a nullable column value from a sqlite3.Row."""
        try:
            val = row[key]
            return val if val is not None else None
        except (IndexError, KeyError):
            return None

    def _row_to_entry(self, row) -> UserActivityLog:
        """Convert a database row to a UserActivityLog dataclass."""
        if row is None:
            return None
        return UserActivityLog(
            id=row["id"],
            telegram_user=row["telegram_user"],
            user_role=row["user_role"],
            action=row["action"],
            report_date=self._safe_get(row, "report_date"),
            report_status=self._safe_get(row, "report_status"),
            contractor_name=self._safe_get(row, "contractor_name"),
            workers=row["workers"] if row["workers"] is not None else None,
            zone=self._safe_get(row, "zone"),
            details=self._safe_get(row, "details"),
            reverted_entry_id=row["reverted_entry_id"] if row["reverted_entry_id"] is not None else None,
            timestamp=row["timestamp"],
        )
