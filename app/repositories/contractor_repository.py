"""Contractor Repository for mventor-ticket-036.

Manages user-added contractors in a dedicated SQLite table.
These contractors appear alongside tables.xlsx contractors
in search and suggestion results.
"""

from typing import Optional

from app.database.manager import DatabaseManager
from app.models.database import Contractor
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContractorRepository:
    """Repository for user-added contractors.

    Each contractor has a name (unique, case-insensitive), optional type,
    and tracking of who added them.
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def add(self, name: str, added_by: str, ctype: Optional[str] = None) -> Optional[Contractor]:
        """Add a new contractor.

        Args:
            name: Contractor name (must be unique, case-insensitive).
            added_by: Telegram user ID of the admin who added this contractor.
            ctype: Optional contractor type (e.g. 'Civil', 'Electrical').

        Returns:
            The created Contractor, or None if a contractor with this
            name already exists.
        """
        name = name.strip()
        if not name:
            return None

        try:
            cursor = self._db.execute(
                "INSERT INTO contractors (name, type, added_by) VALUES (?, ?, ?)",
                (name, ctype, added_by),
            )
            self._db.commit()
            logger.info("Contractor added: %s (type=%s) by %s", name, ctype, added_by)
            return Contractor(
                name=name,
                type=ctype,
            )
        except Exception:
            # UNIQUE constraint violation â€” name already exists
            logger.debug("Contractor '%s' already exists (or DB error)", name)
            return None

    def get_by_name(self, name: str) -> Optional[Contractor]:
        """Find a contractor by exact name (case-insensitive).

        Args:
            name: The contractor name.

        Returns:
            Contractor if found, None otherwise.
        """
        row = self._db.execute(
            "SELECT name, type FROM contractors WHERE LOWER(name) = LOWER(?)",
            (name.strip(),),
        ).fetchone()

        if row is None:
            return None
        return Contractor(name=row["name"], type=row["type"])

    def search(self, query: str, max_results: int = 20) -> list[Contractor]:
        """Search contractors by name (case-insensitive partial match).

        Args:
            query: Search text.
            max_results: Maximum results.

        Returns:
            List of matching contractors sorted by name.
        """
        q = query.strip().lower()
        if not q:
            return []

        rows = self._db.execute(
            "SELECT name, type FROM contractors WHERE LOWER(name) LIKE ? ORDER BY name ASC LIMIT ?",
            (f"%{q}%", max_results),
        ).fetchall()

        return [Contractor(name=row["name"], type=row["type"]) for row in rows]

    def get_all(self) -> list[Contractor]:
        """Get all user-added contractors.

        Returns:
            List of all contractors, sorted by name.
        """
        rows = self._db.execute(
            "SELECT name, type FROM contractors ORDER BY name ASC"
        ).fetchall()
        return [Contractor(name=row["name"], type=row["type"]) for row in rows]

    def delete(self, name: str) -> bool:
        """Delete a contractor by name.

        Args:
            name: Contractor name (case-insensitive).

        Returns:
            True if deleted, False if not found.
        """
        cursor = self._db.execute(
            "DELETE FROM contractors WHERE LOWER(name) = LOWER(?)",
            (name.strip(),),
        )
        self._db.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Contractor deleted: %s", name)
        return deleted

    def count(self) -> int:
        """Get total number of user-added contractors.

        Returns:
            Total count.
        """
        row = self._db.execute("SELECT COUNT(*) AS cnt FROM contractors").fetchone()
        return row["cnt"] if row else 0

    def exists(self, name: str) -> bool:
        """Check if a contractor exists (case-insensitive).

        Args:
            name: Contractor name.

        Returns:
            True if the contractor exists.
        """
        row = self._db.execute(
            "SELECT 1 FROM contractors WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (name.strip(),),
        ).fetchone()
        return row is not None
