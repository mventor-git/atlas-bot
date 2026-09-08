"""
Tests for StatsCacheRepository.

Tests cover:
- Setting and getting stats
- Period-based storage (daily, monthly, yearly)
- Stats invalidation (by key, by period type, all)
- TTL-based expiry
- Cleanup of expired entries
- Missing keys
"""

import time
import json

from app.models.database import StatsCache
from app.repositories.stats_cache_repository import StatsCacheRepository


class TestStatsCacheRepository:
    """Tests for StatsCacheRepository."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        from app.database.manager import DatabaseManager
        self._db = DatabaseManager(self._tmp.name)
        self._repo = StatsCacheRepository(self._db, ttl_minutes=60)

    def teardown_method(self):
        """Clean up after each test."""
        try:
            self._db.close()
        except Exception:
            pass
        import time as _time
        import os
        for attempt in range(5):
            try:
                os.unlink(self._tmp.name)
                return
            except PermissionError:
                if attempt < 4:
                    _time.sleep(0.1)

    def test_get_stats_missing(self):
        """Getting a non-existent key should return None."""
        result = self._repo.get_stats("daily", "2026-07-11")
        assert result is None, (
            "Should return None for non-existent key"
        )

    def test_set_and_get_stats(self):
        """Should store and retrieve stats data."""
        data = {"total_reports": 10, "total_workers": 500}
        self._repo.set_stats("daily", "2026-07-11", data)

        cached = self._repo.get_stats("daily", "2026-07-11")
        assert cached is not None, "Should retrieve cached value"
        assert cached["total_reports"] == 10, (
            f"Expected 10, got {cached['total_reports']}"
        )
        assert cached["total_workers"] == 500, (
            f"Expected 500, got {cached['total_workers']}"
        )

    def test_set_overwrites_existing(self):
        """Setting the same key should overwrite previous data."""
        self._repo.set_stats("daily", "key1", {"value": "old"})
        self._repo.set_stats("daily", "key1", {"value": "new"})

        cached = self._repo.get_stats("daily", "key1")
        assert cached["value"] == "new", (
            f"Expected 'new', got '{cached['value']}'"
        )

    def test_invalidate(self):
        """Should remove a specific key."""
        self._repo.set_stats("daily", "key1", {"value": 1})
        self._repo.set_stats("daily", "key2", {"value": 2})

        assert self._repo.get_stats("daily", "key1") is not None, "key1 should exist"
        self._repo.invalidate("daily", "key1")
        assert self._repo.get_stats("daily", "key1") is None, "key1 should be removed"
        assert self._repo.get_stats("daily", "key2") is not None, "key2 should remain"

    def test_invalidate_period(self):
        """Should invalidate all entries for a period type."""
        self._repo.set_stats("daily", "2026-07-10", {"value": 1})
        self._repo.set_stats("daily", "2026-07-11", {"value": 2})
        self._repo.set_stats("daily", "2026-07-12", {"value": 3})
        self._repo.set_stats("monthly", "2026-07", {"value": 4})

        removed = self._repo.invalidate_period("daily")

        assert removed >= 3, f"Expected at least 3 daily entries removed, got {removed}"
        assert self._repo.get_stats("daily", "2026-07-11") is None, (
            "daily.2026-07-11 should be invalidated"
        )
        assert self._repo.get_stats("monthly", "2026-07") is not None, (
            "monthly.2026-07 should remain"
        )

    def test_invalidate_all(self):
        """Should remove all cached stats."""
        self._repo.set_stats("daily", "key1", {"value": 1})
        self._repo.set_stats("monthly", "key2", {"value": 2})
        self._repo.set_stats("yearly", "key3", {"value": 3})

        self._repo.invalidate_all()

        assert self._repo.get_stats("daily", "key1") is None, "key1 should be gone"
        assert self._repo.get_stats("monthly", "key2") is None, "key2 should be gone"
        assert self._repo.get_stats("yearly", "key3") is None, "key3 should be gone"
        assert self._repo.count() == 0, "Count should be 0 after invalidate_all"

    def test_ttl_expiry(self):
        """Entries past TTL should return None."""
        repo = StatsCacheRepository(self._db, ttl_minutes=0)
        repo.set_stats("daily", "short", {"value": "x"})
        # TTL = 0 minutes means immediately expired
        cached = repo.get_stats("daily", "short")
        assert cached is None, "Should return None for expired entry"

    def test_cleanup_expired(self):
        """Should remove all expired entries."""
        # Use TTL=0 so entries expire immediately
        repo = StatsCacheRepository(self._db, ttl_minutes=0)
        repo.set_stats("daily", "expired1", {"value": 1})
        repo.set_stats("daily", "expired2", {"value": 2})

        cleaned = repo.cleanup_expired()
        assert cleaned >= 1, f"Expected at least 1 cleaned, got {cleaned}"
        assert repo.get_stats("daily", "expired1") is None, (
            "expired1 should be gone"
        )

    def test_count(self):
        """Should return total cached entries count."""
        assert self._repo.count() == 0, "Should start with 0"
        self._repo.set_stats("daily", "key1", {"v": 1})
        assert self._repo.count() == 1, f"Expected 1, got {self._repo.count()}"
        self._repo.set_stats("daily", "key2", {"v": 2})
        assert self._repo.count() == 2, f"Expected 2, got {self._repo.count()}"

    def test_stats_data_is_dict(self):
        """Should retrieve stats data as a dict (not a string)."""
        self._repo.set_stats("daily", "key1", {"total": 100})
        cached = self._repo.get_stats("daily", "key1")
        assert isinstance(cached, dict), (
            f"Expected dict, got {type(cached)}"
        )
        assert cached["total"] == 100, (
            f"Expected 100, got {cached['total']}"
        )

    def test_nested_data(self):
        """Should handle nested dict data."""
        data = {
            "contractors": {
                "Civil Co": {"workers": 100, "days": 5},
                "Electric Inc": {"workers": 50, "days": 3},
            }
        }
        self._repo.set_stats("daily", "nested.test", data)
        cached = self._repo.get_stats("daily", "nested.test")
        assert cached["contractors"]["Civil Co"]["workers"] == 100, (
            "Nested data should be preserved"
        )

    def test_different_periods(self):
        """Should store and retrieve for different period types."""
        daily_data = {"workers": 10}
        monthly_data = {"workers": 300}
        yearly_data = {"workers": 3650}

        self._repo.set_stats("daily", "2026-07-11", daily_data)
        self._repo.set_stats("monthly", "2026-07", monthly_data)
        self._repo.set_stats("yearly", "2026", yearly_data)

        assert self._repo.get_stats("daily", "2026-07-11") == daily_data, (
            "Daily data mismatch"
        )
        assert self._repo.get_stats("monthly", "2026-07") == monthly_data, (
            "Monthly data mismatch"
        )
        assert self._repo.get_stats("yearly", "2026") == yearly_data, (
            "Yearly data mismatch"
        )

    def test_ttl_property(self):
        """Should be able to get/set TTL."""
        assert self._repo.ttl_minutes == 60, f"Expected 60, got {self._repo.ttl_minutes}"
        self._repo.ttl_minutes = 30
        assert self._repo.ttl_minutes == 30, f"Expected 30, got {self._repo.ttl_minutes}"
