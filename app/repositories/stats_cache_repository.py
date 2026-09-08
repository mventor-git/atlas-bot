"""
Repository for pre-computed statistics cache. (NEW v2.0)

Stores aggregated statistics by period (daily/monthly/yearly)
to avoid expensive SQL aggregation queries on every dashboard load.
Cache entries have a configurable TTL and are refreshed on expiry.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from app.database.manager import DatabaseManager
from app.models.database import StatsCache
from app.repositories.base import BaseRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StatsCacheRepository(BaseRepository[StatsCache]):
    """Repository for pre-computed statistics cache.

    Stores JSON data blobs keyed by (period, period_key) for
    fast dashboard and statistics display.

    Usage:
        repo = StatsCacheRepository(db_manager, ttl_minutes=60)
        repo.set_stats("daily", "2026-07-11", {"workers": 15})
        data = repo.get_stats("daily", "2026-07-11")  # Returns None if expired
    """

    def __init__(self, db_manager: DatabaseManager, ttl_minutes: int = 60) -> None:
        """Initialize the repository.

        Args:
            db_manager: The database manager instance.
            ttl_minutes: Cache TTL in minutes (default 60).
        """
        self._db = db_manager
        self._ttl_minutes = ttl_minutes

    @property
    def ttl_minutes(self) -> int:
        """Get the current cache TTL in minutes."""
        return self._ttl_minutes

    @ttl_minutes.setter
    def ttl_minutes(self, value: int) -> None:
        """Set the cache TTL in minutes."""
        self._ttl_minutes = value

    # --- High-level API ---

    def get_stats(
        self, period: str, period_key: str
    ) -> Optional[dict[str, Any]]:
        """Get cached statistics for a period.

        Returns None if the cache entry doesn't exist or has expired.

        Args:
            period: Period type ('daily', 'monthly', 'yearly').
            period_key: Period identifier (e.g., '2026-07-11', '2026-07', '2026').

        Returns:
            Dict of statistics data, or None if not cached or expired.
        """
        entry = self._find_entry(period, period_key)
        if entry is None:
            return None

        # Check TTL
        if self._is_expired(entry):
            logger.debug("Cache expired for %s/%s", period, period_key)
            self.delete(entry.id)
            return None

        try:
            return json.loads(entry.data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Invalid JSON in stats_cache: %s", e)
            self.delete(entry.id)
            return None

    def set_stats(
        self, period: str, period_key: str, data: dict[str, Any]
    ) -> StatsCache:
        """Cache statistics for a period.

        Creates or updates the cache entry.

        Args:
            period: Period type ('daily', 'monthly', 'yearly').
            period_key: Period identifier.
            data: Statistics data dict to cache.

        Returns:
            The StatsCache entry.
        """
        json_data = json.dumps(data, ensure_ascii=False)
        now = datetime.now().isoformat()

        # Check if entry already exists
        existing = self._find_entry(period, period_key)
        if existing is not None:
            # Update existing
            existing.data = json_data
            existing.computed_at = now
            return self.update(existing)

        # Create new
        entry = StatsCache(
            period=period,
            period_key=period_key,
            data=json_data,
            computed_at=now,
        )
        return self.add(entry)

    def invalidate(self, period: str, period_key: str) -> bool:
        """Remove a specific cache entry.

        Args:
            period: Period type.
            period_key: Period identifier.

        Returns:
            True if removed, False if not found.
        """
        entry = self._find_entry(period, period_key)
        if entry is None or entry.id is None:
            return False
        return self.delete(entry.id)

    def invalidate_period(self, period: str) -> int:
        """Remove all cache entries for a period type.

        Useful when data changes that affects all stats.

        Args:
            period: Period type to invalidate.

        Returns:
            Number of entries removed.
        """
        cursor = self._db.execute(
            "DELETE FROM stats_cache WHERE period = ?", (period,)
        )
        self._db.commit()
        return cursor.rowcount

    def invalidate_all(self) -> int:
        """Remove ALL cached statistics.

        Returns:
            Number of entries removed.
        """
        cursor = self._db.execute("DELETE FROM stats_cache")
        self._db.commit()
        count = cursor.rowcount
        logger.info("Invalidated all stats cache (%d entries)", count)
        return count

    def cleanup_expired(self) -> int:
        """Remove all expired cache entries.

        Returns:
            Number of entries removed.
        """
        all_entries = self.get_all()
        expired = [e for e in all_entries if self._is_expired(e)]
        for entry in expired:
            if entry.id is not None:
                self.delete(entry.id)
        count = len(expired)
        if count:
            logger.info("Cleaned up %d expired stats cache entries", count)
        return count

    # --- BaseRepository implementation ---

    def get_by_id(self, entity_id: int) -> Optional[StatsCache]:
        row = self._db.execute(
            "SELECT * FROM stats_cache WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_all(self) -> list[StatsCache]:
        rows = self._db.execute(
            "SELECT * FROM stats_cache ORDER BY period, period_key"
        ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def add(self, entity: StatsCache) -> StatsCache:
        cursor = self._db.execute(
            """INSERT INTO stats_cache
               (period, period_key, data, computed_at)
               VALUES (?, ?, ?, ?)""",
            (entity.period, entity.period_key, entity.data, entity.computed_at),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def update(self, entity: StatsCache) -> StatsCache:
        if entity.id is None:
            raise DatabaseError("Cannot update a StatsCache without an ID.")
        self._db.execute(
            """UPDATE stats_cache
               SET data=?, computed_at=?
               WHERE id=?""",
            (entity.data, entity.computed_at, entity.id),
        )
        self._db.commit()
        return entity

    def delete(self, entity_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM stats_cache WHERE id = ?", (entity_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM stats_cache"
        ).fetchone()
        return row["cnt"] if row else 0

    # --- Private ---

    def _find_entry(
        self, period: str, period_key: str
    ) -> Optional[StatsCache]:
        """Find a cache entry by period and key.

        Args:
            period: Period type.
            period_key: Period identifier.

        Returns:
            StatsCache entry or None.
        """
        row = self._db.execute(
            """SELECT * FROM stats_cache
               WHERE period = ? AND period_key = ?""",
            (period, period_key),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def _is_expired(self, entry: StatsCache) -> bool:
        """Check if a cache entry is expired based on TTL.

        Args:
            entry: The StatsCache entry to check.

        Returns:
            True if expired.
        """
        try:
            computed = datetime.fromisoformat(entry.computed_at)
            return (datetime.now() - computed) > timedelta(minutes=self._ttl_minutes)
        except (ValueError, TypeError):
            return True  # If timestamp is malformed, treat as expired

    @staticmethod
    def _row_to_model(row) -> StatsCache:
        return StatsCache(
            id=row["id"],
            period=row["period"],
            period_key=row["period_key"],
            data=row["data"],
            computed_at=row["computed_at"],
        )
