"""Site membership repository (008 tenancy).

Membership = who belongs where with what grants. Reads/writes are
plain CRUD; authorization decisions live in AuthorizationService.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.database import SiteMembership
from app.repositories.base import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MembershipRepository(BaseRepository[SiteMembership]):
    """Persistence for user_site_memberships."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def add(self, entity: SiteMembership) -> SiteMembership:
        cursor = self._db.execute(
            """INSERT INTO user_site_memberships
               (chat_id, site_id, capabilities, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                entity.chat_id,
                entity.site_id,
                json.dumps(entity.capabilities or [], ensure_ascii=False),
                entity.status,
                entity.created_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_by_id(self, entity_id: int, site_id: str | None = None) -> Optional[SiteMembership]:
        row = self._db.execute(
            "SELECT * FROM user_site_memberships WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self, site_id: str | None = None) -> list[SiteMembership]:
        rows = self._db.execute(
            """SELECT * FROM user_site_memberships WHERE site_id = ?
               ORDER BY created_at ASC""",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def update(self, entity: SiteMembership) -> SiteMembership:
        if entity.id is None:
            raise ValueError("Cannot update a membership without an ID.")
        self._db.execute(
            """UPDATE user_site_memberships
               SET capabilities=?, status=? WHERE id=?""",
            (
                json.dumps(entity.capabilities or [], ensure_ascii=False),
                entity.status,
                entity.id,
            ),
        )
        self._db.commit()
        return entity

    def delete(self, entity_id: int, site_id: str | None = None) -> bool:
        cursor = self._db.execute(
            "DELETE FROM user_site_memberships WHERE id = ?", (entity_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self, site_id: str | None = None) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM user_site_memberships WHERE site_id = ?",
            (site_id or driver.site_id(),),
        ).fetchone()
        return row["cnt"] if row else 0

    def grant(self, chat_id: str, site_id: str,
              capabilities: list | None = None) -> SiteMembership:
        """Grant (or refresh) an active membership."""
        existing = self.find(chat_id, site_id)
        if existing is not None:
            existing.capabilities = capabilities or []
            existing.status = "active"
            return self.update(existing)
        return self.add(SiteMembership(
            chat_id=chat_id, site_id=site_id,
            capabilities=capabilities or [], status="active"))

    def suspend(self, chat_id: str, site_id: str) -> bool:
        """Suspend a membership (denies everything, keeps history)."""
        existing = self.find(chat_id, site_id)
        if existing is None:
            return False
        existing.status = "suspended"
        self.update(existing)
        return True

    def revoke(self, chat_id: str, site_id: str) -> bool:
        """Remove a membership entirely (a fresh grant is required)."""
        existing = self.find(chat_id, site_id)
        if existing is None:
            return False
        return self.delete(existing.id)

    def active_chat_ids(self, site_id: str | None = None) -> list[str]:
        """Chat IDs holding an ACTIVE membership at a site (Phase 1)."""
        rows = self._db.execute(
            """SELECT chat_id FROM user_site_memberships
               WHERE site_id = ? AND status = 'active'
               ORDER BY chat_id ASC""",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [str(r["chat_id"]) for r in rows]

    def find(self, chat_id: str, site_id: str) -> Optional[SiteMembership]:
        """Active-or-not membership row for a user+site pair."""
        row = self._db.execute(
            """SELECT * FROM user_site_memberships
               WHERE chat_id = ? AND site_id = ?""",
            (chat_id, site_id),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def active_for_user(self, chat_id: str) -> list[SiteMembership]:
        """All ACTIVE memberships of a user (any site)."""
        rows = self._db.execute(
            """SELECT * FROM user_site_memberships
               WHERE chat_id = ? AND status = 'active'
               ORDER BY site_id ASC""",
            (chat_id,),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def migrate_from_users(self, user_repo) -> int:
        """Backfill memberships from legacy users.site_id (idempotent).

        Every user with a site gets one active membership with no
        explicit grants (role defaults apply). Returns rows created.
        """
        created = 0
        for user in user_repo.get_all_users():
            site = (user.site_id or "default").strip() or "default"
            if self.find(user.chat_id, site) is None:
                self.grant(user.chat_id, site, [])
                created += 1
        if created:
            logger.info("Migrated %d users.site_id to memberships.", created)
        return created

    @staticmethod
    def _row_to_model(row) -> SiteMembership:
        raw = row["capabilities"] if "capabilities" in row.keys() else "[]"
        try:
            capabilities = json.loads(raw or "[]")
        except (TypeError, ValueError):
            capabilities = []
        return SiteMembership(
            id=row["id"],
            chat_id=str(row["chat_id"]),
            site_id=row["site_id"],
            capabilities=capabilities,
            status=row["status"],
            created_at=row["created_at"],
        )
