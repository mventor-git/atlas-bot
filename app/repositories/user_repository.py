"""
User repository for Labor-Report.

Manages Telegram users and their roles (super_admin, admin, user, pending, rejected).
"""

from datetime import datetime
from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.database import User
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UserRepository:
    """Repository for managing Telegram users and roles."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    # --- Queries ---

    def get_by_chat_id(self, chat_id: str) -> Optional[User]:
        """Get a user by their Telegram chat ID.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            User dataclass or None if not found.
        """
        cursor = self._db.execute(
            "SELECT * FROM users WHERE chat_id = ?", (chat_id,)
        )
        row = cursor.fetchone()
        return self._row_to_user(row) if row else None

    def get_all_users(self) -> list[User]:
        """Get all registered users ordered by creation time.

        Returns:
            List of User dataclasses.
        """
        cursor = self._db.execute(
            "SELECT * FROM users ORDER BY created_at ASC"
        )
        return [self._row_to_user(row) for row in cursor.fetchall()]

    def get_users_by_role(self, role: str) -> list[User]:
        """Get all users with a specific role.

        Args:
            role: 'superadmin', 'project_manager', 'executive_engineer', 'admin', 'normal_user', 'viewer', 'pending', or 'rejected'.

        Returns:
            List of User dataclasses.
        """
        cursor = self._db.execute(
            "SELECT * FROM users WHERE role = ? ORDER BY created_at ASC", (role,)
        )
        return [self._row_to_user(row) for row in cursor.fetchall()]

    def get_pending_users(self) -> list[User]:
        """Get all users awaiting approval.

        Returns:
            List of pending User dataclasses.
        """
        return self.get_users_by_role("pending")

    def count_by_role(self, role: str) -> int:
        """Count users with a specific role.

        Args:
            role: The role to count.

        Returns:
            Number of users with that role.
        """
        cursor = self._db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE role = ?", (role,)
        )
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def exists(self, chat_id: str) -> bool:
        """Check if a user exists in the database.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user exists.
        """
        cursor = self._db.execute(
            "SELECT 1 FROM users WHERE chat_id = ?", (chat_id,)
        )
        return cursor.fetchone() is not None

    # --- Mutations ---

    def upsert(self, user: User) -> User:
        """Insert or update a user.

        If a user with the same chat_id exists, updates their record.
        Otherwise, inserts a new record.

        Args:
            user: The User dataclass to persist.

        Returns:
            The User dataclass with the database ID populated.

        Raises:
            DatabaseError: If the operation fails.
        """
        now = datetime.now().isoformat()
        user.updated_at = now

        existing = self.get_by_chat_id(user.chat_id)
        if existing:
            self._db.execute(
                """UPDATE users SET
                    role = ?, username = ?, first_name = ?,
                    approved_by = ?, approved_at = ?, updated_at = ?
                WHERE chat_id = ?""",
                (
                    user.role, user.username, user.first_name,
                    user.approved_by, user.approved_at, now,
                    user.chat_id,
                ),
            )
            user.id = existing.id
            logger.debug("Updated user %s: role=%s", user.chat_id, user.role)
        else:
            cursor = self._db.execute(
                """INSERT INTO users
                    (chat_id, role, username, first_name, site_id, created_at,
                     approved_by, approved_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user.chat_id, user.role, user.username, user.first_name,
                    user.site_id or "default",
                    user.created_at, user.approved_by, user.approved_at, now,
                ),
            )
            user.id = cursor.lastrowid
            user.site_id = user.site_id or "default"
            logger.debug("Inserted user %s: role=%s", user.chat_id, user.role)

        self._db.commit()
        return user

    def set_role(self, chat_id: str, new_role: str,
                 approved_by: Optional[str] = None) -> Optional[User]:
        """Change a user's role.

        Args:
            chat_id: Telegram chat ID.
            new_role: New role ('superadmin', 'project_manager', 'executive_engineer', 'admin', 'normal_user', 'viewer', 'pending', 'rejected').
            approved_by: Chat ID of the admin making the change.

        Returns:
            Updated User dataclass, or None if user not found.
        """
        user = self.get_by_chat_id(chat_id)
        if user is None:
            return None

        user.role = new_role
        user.updated_at = datetime.now().isoformat()

        if new_role in ("user", "admin") and approved_by:
            user.approved_by = approved_by
            user.approved_at = datetime.now().isoformat()

        self._db.execute(
            """UPDATE users SET
                role = ?, approved_by = ?, approved_at = ?, updated_at = ?
            WHERE chat_id = ?""",
            (user.role, user.approved_by, user.approved_at, user.updated_at, chat_id),
        )
        self._db.commit()

        logger.info("User %s role changed to %s by %s", chat_id, new_role, approved_by)
        return user

    def get_users_by_site(self, site_id: str | None = None) -> list[User]:
        """Get all users assigned to a site, ordered by creation time."""
        rows = self._db.execute(
            "SELECT * FROM users WHERE site_id = ? ORDER BY created_at ASC",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_user(row) for row in rows]

    def set_site(self, chat_id: str, site_id: str) -> Optional[User]:
        """Assign a user to a site."""
        user = self.get_by_chat_id(chat_id)
        if user is None:
            return None
        user.site_id = site_id
        user.updated_at = datetime.now().isoformat()
        self._db.execute(
            "UPDATE users SET site_id = ?, updated_at = ? WHERE chat_id = ?",
            (site_id, user.updated_at, chat_id),
        )
        self._db.commit()
        logger.info("User %s assigned to site %s", chat_id, site_id)
        return user

    def delete(self, chat_id: str) -> bool:
        """Delete a user by chat ID.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if a user was deleted, False if not found.
        """
        cursor = self._db.execute(
            "DELETE FROM users WHERE chat_id = ?", (chat_id,)
        )
        self._db.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Deleted user %s", chat_id)
        return deleted

    # --- Helpers ---

    @staticmethod
    @staticmethod
    def _safe_get(row, key: str):
        """Safely get a nullable column value from a sqlite3.Row."""
        try:
            val = row[key]
            return val if val is not None else None
        except (IndexError, KeyError):
            return None

    def _row_to_user(self, row) -> Optional[User]:
        """Convert a database row to a User dataclass."""
        if row is None:
            return None
        return User(
            id=row["id"],
            chat_id=str(row["chat_id"]),
            role=row["role"],
            username=self._safe_get(row, "username"),
            first_name=self._safe_get(row, "first_name"),
            created_at=row["created_at"],
            approved_by=self._safe_get(row, "approved_by"),
            approved_at=self._safe_get(row, "approved_at"),
            site_id=self._safe_get(row, "site_id") or "default",
            updated_at=row["updated_at"],
        )
