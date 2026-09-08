"""
Repository for per-user favorite contractors. (NEW v2.0)

Favorite contractors appear first in search results and smart suggestions.
Each user has their own isolated favorites list.
"""

from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.models.database import FavoriteContractor
from app.repositories.base import BaseRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FavoritesRepository(BaseRepository[FavoriteContractor]):
    """Repository for per-user favorite contractors.

    Provides CRUD for favorites and integrates with the
    contractor search and suggestion systems.

    Usage:
        repo = FavoritesRepository(db_manager)
        repo.add_favorite("user123", "Civil Co")
        favs = repo.get_favorites("user123")
        repo.remove_favorite("user123", "Civil Co")
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the repository.

        Args:
            db_manager: The database manager instance.
        """
        self._db = db_manager

    # --- High-level API ---

    def add_favorite(
        self,
        telegram_user: str,
        contractor_name: str,
        contractor_code: Optional[str] = None,
    ) -> FavoriteContractor:
        """Add a contractor to user's favorites.

        If already favorited, returns the existing record.

        Args:
            telegram_user: The Telegram user identifier.
            contractor_name: Contractor name to favorite.
            contractor_code: Optional contractor code.

        Returns:
            The FavoriteContractor object.

        Raises:
            DatabaseError: If the UNIQUE constraint is violated unexpectedly.
        """
        # Check if already favorited
        existing = self._find_favorite(telegram_user, contractor_name)
        if existing is not None:
            logger.debug(
                "Contractor '%s' already favorited by user %s",
                contractor_name, telegram_user,
            )
            return existing

        fav = FavoriteContractor(
            telegram_user=telegram_user,
            contractor_name=contractor_name,
            contractor_code=contractor_code,
            created_at=datetime.now().isoformat(),
        )
        result = self.add(fav)
        logger.info(
            "Favorite added: user=%s, contractor=%s", telegram_user, contractor_name,
        )
        return result

    def remove_favorite(self, telegram_user: str, contractor_name: str) -> bool:
        """Remove a contractor from user's favorites.

        Args:
            telegram_user: The Telegram user identifier.
            contractor_name: Contractor name to unfavorite.

        Returns:
            True if removed, False if not found.
        """
        existing = self._find_favorite(telegram_user, contractor_name)
        if existing is None or existing.id is None:
            logger.debug(
                "Favorite not found: user=%s, contractor=%s",
                telegram_user, contractor_name,
            )
            return False

        result = self.delete(existing.id)
        if result:
            logger.info(
                "Favorite removed: user=%s, contractor=%s",
                telegram_user, contractor_name,
            )
        return result

    def get_favorites(self, telegram_user: str) -> list[FavoriteContractor]:
        """Get all favorites for a user, ordered by creation date.

        Args:
            telegram_user: The Telegram user identifier.

        Returns:
            List of FavoriteContractor objects.
        """
        rows = self._db.execute(
            """SELECT * FROM favorites
               WHERE telegram_user = ?
               ORDER BY created_at DESC""",
            (telegram_user,),
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def get_favorite_names(self, telegram_user: str) -> list[str]:
        """Get favorite contractor names for a user.

        Convenience method for quick lookups.

        Args:
            telegram_user: The Telegram user identifier.

        Returns:
            List of contractor names.
        """
        favs = self.get_favorites(telegram_user)
        return [f.contractor_name for f in favs]

    def is_favorite(self, telegram_user: str, contractor_name: str) -> bool:
        """Check if a contractor is favorited by a user.

        Args:
            telegram_user: The Telegram user identifier.
            contractor_name: Contractor name to check.

        Returns:
            True if favorited.
        """
        return self._find_favorite(telegram_user, contractor_name) is not None

    def count_by_user(self, telegram_user: str) -> int:
        """Count favorites for a user.

        Args:
            telegram_user: The Telegram user identifier.

        Returns:
            Number of favorites.
        """
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM favorites WHERE telegram_user = ?",
            (telegram_user,),
        ).fetchone()
        return row["cnt"] if row else 0

    # --- BaseRepository implementation ---

    def get_by_id(self, entity_id: int) -> Optional[FavoriteContractor]:
        row = self._db.execute(
            "SELECT * FROM favorites WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self) -> list[FavoriteContractor]:
        rows = self._db.execute(
            "SELECT * FROM favorites ORDER BY telegram_user, created_at DESC"
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def add(self, entity: FavoriteContractor) -> FavoriteContractor:
        cursor = self._db.execute(
            """INSERT INTO favorites
               (telegram_user, contractor_name, contractor_code, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                entity.telegram_user,
                entity.contractor_name,
                entity.contractor_code,
                entity.created_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def update(self, entity: FavoriteContractor) -> FavoriteContractor:
        if entity.id is None:
            raise DatabaseError("Cannot update a FavoriteContractor without an ID.")
        self._db.execute(
            """UPDATE favorites
               SET contractor_name=?, contractor_code=?
               WHERE id=?""",
            (entity.contractor_name, entity.contractor_code, entity.id),
        )
        self._db.commit()
        return entity

    def delete(self, entity_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM favorites WHERE id = ?", (entity_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) as cnt FROM favorites").fetchone()
        return row["cnt"] if row else 0

    # --- Private ---

    def _find_favorite(
        self, telegram_user: str, contractor_name: str
    ) -> Optional[FavoriteContractor]:
        """Find a specific favorite by user and contractor name.

        Args:
            telegram_user: The Telegram user identifier.
            contractor_name: Contractor name.

        Returns:
            FavoriteContractor if found, None otherwise.
        """
        row = self._db.execute(
            """SELECT * FROM favorites
               WHERE telegram_user = ? AND contractor_name = ?""",
            (telegram_user, contractor_name),
        ).fetchone()
        return self._row_to_model(row) if row else None

    @staticmethod
    def _row_to_model(row) -> FavoriteContractor:
        return FavoriteContractor(
            id=row["id"],
            telegram_user=row["telegram_user"],
            contractor_name=row["contractor_name"],
            contractor_code=row["contractor_code"],
            created_at=row["created_at"],
        )
