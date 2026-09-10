"""
Repository for recently used contractors.

Tracks which contractors each Telegram user has recently added
to their reports, to speed up future report creation.
"""

from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.models.database import RecentContractor
from app.repositories.base import BaseRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Additional SQL for the recent_contractors table
RECENT_CONTRACTORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS recent_contractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_name TEXT NOT NULL,
    telegram_user TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    last_used TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(contractor_name, telegram_user)
);

CREATE INDEX IF NOT EXISTS idx_recent_contractors_user
    ON recent_contractors(telegram_user, last_used DESC);
"""


class RecentContractorRepository(BaseRepository[RecentContractor]):
    """Tracks recently used contractors per Telegram user.

    Stores which contractors each user has used, with usage count
    and timestamp for sorting by recency.

    Usage:
        repo = RecentContractorRepository(db_manager)
        recent = repo.get_recent_for_user("user123", limit=5)
        repo.record_usage("user123", "Civil Contractor")
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the repository.

        Args:
            db_manager: The database manager instance.
        """
        self._db = db_manager
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the recent_contractors table if it doesn't exist."""
        # Split multi-statement SQL since execute() only handles one statement
        for statement in RECENT_CONTRACTORS_TABLE_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    self._db.execute(stmt)
                    self._db.commit()
                except DatabaseError as e:
                    logger.warning("Failed to execute: %s ... %s", stmt[:50], e)

    # --- Public API ---

    def get_recent_for_user(self, telegram_user: str, limit: int = 10) -> list[RecentContractor]:
        """Get recently used contractors for a user, ordered by last used.

        Args:
            telegram_user: The Telegram user identifier.
            limit: Maximum number of results.

        Returns:
            List of RecentContractor entries, most recent first.
        """
        rows = self._db.execute(
            """SELECT * FROM recent_contractors
               WHERE telegram_user = ?
               ORDER BY last_used DESC
               LIMIT ?""",
            (telegram_user, limit),
        ).fetchall()

        return [self._row_to_model(row) for row in rows]

    def record_usage(self, telegram_user: str, contractor_name: str) -> None:
        """Record that a user used a contractor.

        If the contractor already exists for this user, increments
        the use count and updates the timestamp.

        Args:
            telegram_user: The Telegram user identifier.
            contractor_name: The contractor name used.
        """
        now = datetime.now().isoformat()

        # Check if existing record
        existing = self._db.execute(
            """SELECT id FROM recent_contractors
               WHERE contractor_name = ? AND telegram_user = ?""",
            (contractor_name, telegram_user),
        ).fetchone()

        if existing:
            self._db.execute(
                """UPDATE recent_contractors
                   SET last_used = ?, use_count = use_count + 1
                   WHERE id = ?""",
                (now, existing["id"]),
            )
        else:
            self._db.execute(
                """INSERT INTO recent_contractors
                   (contractor_name, telegram_user, last_used, use_count)
                   VALUES (?, ?, ?, 1)""",
                (contractor_name, telegram_user, now),
            )

        self._db.commit()
        logger.debug("Recorded contractor usage: user=%s, contractor=%s", telegram_user, contractor_name)

    def record_usage_batch(self, telegram_user: str, contractor_names: list[str]) -> None:
        """Record usage for multiple contractors at once.

        Args:
            telegram_user: The Telegram user identifier.
            contractor_names: List of contractor names used.
        """
        for name in contractor_names:
            self.record_usage(telegram_user, name)

    def get_recent_names(self, telegram_user: str, limit: int = 10) -> list[str]:
        """Get recently used contractor names for a user.

        Convenience method that returns just the names.

        Args:
            telegram_user: The Telegram user identifier.
            limit: Maximum number of results.

        Returns:
            List of contractor names, most recent first.
        """
        recent = self.get_recent_for_user(telegram_user, limit)
        return [r.contractor_name for r in recent]

    def clear_for_user(self, telegram_user: str) -> int:
        """Clear all recent contractor records for a user.

        Args:
            telegram_user: The Telegram user identifier.

        Returns:
            Number of records deleted.
        """
        cursor = self._db.execute(
            "DELETE FROM recent_contractors WHERE telegram_user = ?",
            (telegram_user,),
        )
        self._db.commit()
        deleted = cursor.rowcount
        logger.info("Cleared %d recent contractor records for user %s", deleted, telegram_user)
        return deleted

    def get_frequent_global(self, limit: int = 5) -> list[RecentContractor]:
        """Get most frequently used contractors across all users.

        Aggregates by contractor_name, summing use_count across all users,
        ordered by total usage descending.

        Args:
            limit: Maximum number of results (default 5).

        Returns:
            List of RecentContractor objects (telegram_user will be empty
            since this is a cross-user aggregation).
        """
        rows = self._db.execute(
            """SELECT contractor_name, SUM(use_count) as total_use
               FROM recent_contractors
               GROUP BY contractor_name
               ORDER BY total_use DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            RecentContractor(
                contractor_name=row["contractor_name"],
                telegram_user="",
                last_used="",
                use_count=row["total_use"],
            )
            for row in rows
        ]

    # --- BaseRepository implementation ---

    def get_by_id(self, entity_id: int) -> Optional[RecentContractor]:
        row = self._db.execute(
            "SELECT * FROM recent_contractors WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self) -> list[RecentContractor]:
        rows = self._db.execute(
            "SELECT * FROM recent_contractors ORDER BY last_used DESC"
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def add(self, entity: RecentContractor) -> RecentContractor:
        cursor = self._db.execute(
            """INSERT INTO recent_contractors
               (contractor_name, telegram_user, last_used, use_count)
               VALUES (?, ?, ?, ?)""",
            (entity.contractor_name, entity.telegram_user, entity.last_used, entity.use_count),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def update(self, entity: RecentContractor) -> RecentContractor:
        if entity.id is None:
            raise DatabaseError("Cannot update a RecentContractor without an ID.")
        self._db.execute(
            """UPDATE recent_contractors
               SET contractor_name=?, telegram_user=?, last_used=?, use_count=?
               WHERE id=?""",
            (entity.contractor_name, entity.telegram_user, entity.last_used, entity.use_count, entity.id),
        )
        self._db.commit()
        return entity

    def delete(self, entity_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM recent_contractors WHERE id=?", (entity_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) as cnt FROM recent_contractors").fetchone()
        return row["cnt"] if row else 0

    # --- Private ---

    @staticmethod
    def _row_to_model(row) -> RecentContractor:
        return RecentContractor(
            id=row["id"],
            contractor_name=row["contractor_name"],
            telegram_user=row["telegram_user"],
            last_used=row["last_used"],
            use_count=row["use_count"],
        )
