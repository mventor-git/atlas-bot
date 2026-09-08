"""
Tests for FavoritesRepository.

Tests cover:
- Adding favorites
- Removing favorites
- Listing favorites
- Per-user isolation
- Duplicate prevention
- Favorite checks
"""

import pytest

from app.models.database import FavoriteContractor
from app.repositories.favorites_repository import FavoritesRepository


class TestFavoritesRepository:
    """Tests for FavoritesRepository."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        from app.database.manager import DatabaseManager
        self._db = DatabaseManager(self._tmp.name)
        self._repo = FavoritesRepository(self._db)

    def teardown_method(self):
        """Clean up after each test."""
        try:
            self._db.close()
        except Exception:
            pass
        import time
        import os
        for attempt in range(5):
            try:
                os.unlink(self._tmp.name)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1)

    def test_add_favorite(self):
        """Adding a favorite should return it with an ID."""
        fav = self._repo.add_favorite("user1", "Civil Co")
        assert fav.id is not None, "Favorite should have an ID"
        assert fav.telegram_user == "user1", (
            f"Expected 'user1', got '{fav.telegram_user}'"
        )
        assert fav.contractor_name == "Civil Co", (
            f"Expected 'Civil Co', got '{fav.contractor_name}'"
        )

    def test_add_favorite_with_code(self):
        """Should support adding a favorite with contractor code."""
        fav = self._repo.add_favorite("user1", "Civil Co", contractor_code="CIV-001")
        assert fav.contractor_code == "CIV-001", (
            f"Expected 'CIV-001', got '{fav.contractor_code}'"
        )

    def test_add_duplicate_favorite_returns_existing(self):
        """Adding the same favorite twice should return the existing record."""
        fav1 = self._repo.add_favorite("user1", "Civil Co")
        fav2 = self._repo.add_favorite("user1", "Civil Co")

        assert fav1.id == fav2.id, (
            "Duplicate add should return the same record"
        )
        assert self._repo.count_by_user("user1") == 1, (
            "Should only have 1 favorite"
        )

    def test_remove_favorite(self):
        """Should remove a favorite and return True."""
        self._repo.add_favorite("user1", "Civil Co")
        removed = self._repo.remove_favorite("user1", "Civil Co")
        assert removed is True, "Remove should return True"
        assert self._repo.count_by_user("user1") == 0, (
            "Should have 0 favorites after remove"
        )

    def test_remove_nonexistent_favorite(self):
        """Removing a non-existent favorite should return False."""
        removed = self._repo.remove_favorite("user1", "Nonexistent Co")
        assert removed is False, "Remove non-existent should return False"

    def test_get_favorites(self):
        """Should return all favorites for a user."""
        self._repo.add_favorite("user1", "Civil Co")
        self._repo.add_favorite("user1", "Electric Inc")
        self._repo.add_favorite("user1", "Plumbing Co")

        favs = self._repo.get_favorites("user1")
        assert len(favs) == 3, (
            f"Expected 3 favorites, got {len(favs)}"
        )
        names = [f.contractor_name for f in favs]
        assert "Civil Co" in names, "Civil Co should be in favorites"
        assert "Electric Inc" in names, "Electric Inc should be in favorites"

    def test_get_favorites_empty(self):
        """Should return empty list for user with no favorites."""
        favs = self._repo.get_favorites("user1")
        assert favs == [], "Should return empty list"

    def test_per_user_isolation(self):
        """Favorites should be isolated per user."""
        self._repo.add_favorite("user1", "Civil Co")
        self._repo.add_favorite("user2", "Electric Inc")

        user1_favs = self._repo.get_favorites("user1")
        user2_favs = self._repo.get_favorites("user2")

        assert len(user1_favs) == 1, (
            f"Expected 1 favorite for user1, got {len(user1_favs)}"
        )
        assert len(user2_favs) == 1, (
            f"Expected 1 favorite for user2, got {len(user2_favs)}"
        )
        assert user1_favs[0].contractor_name == "Civil Co", (
            f"Expected 'Civil Co', got '{user1_favs[0].contractor_name}'"
        )
        assert user2_favs[0].contractor_name == "Electric Inc", (
            f"Expected 'Electric Inc', got '{user2_favs[0].contractor_name}'"
        )

    def test_get_favorite_names(self):
        """Should return just the contractor names."""
        self._repo.add_favorite("user1", "Civil Co")
        self._repo.add_favorite("user1", "Electric Inc")

        names = self._repo.get_favorite_names("user1")
        assert names == ["Electric Inc", "Civil Co"] or names == ["Civil Co", "Electric Inc"], (
            f"Unexpected names order: {names}"
        )
        assert "Civil Co" in names, "Civil Co should be in names"
        assert "Electric Inc" in names, "Electric Inc should be in names"

    def test_is_favorite(self):
        """Should correctly check if contractor is favorited."""
        self._repo.add_favorite("user1", "Civil Co")

        assert self._repo.is_favorite("user1", "Civil Co") is True, (
            "Civil Co should be a favorite"
        )
        assert self._repo.is_favorite("user1", "Nonexistent") is False, (
            "Nonexistent should not be a favorite"
        )

    def test_count_by_user(self):
        """Should return the count of favorites for a user."""
        assert self._repo.count_by_user("user1") == 0, "Should start at 0"
        self._repo.add_favorite("user1", "Civil Co")
        assert self._repo.count_by_user("user1") == 1, (
            f"Expected 1, got {self._repo.count_by_user('user1')}"
        )
        self._repo.add_favorite("user1", "Electric Inc")
        assert self._repo.count_by_user("user1") == 2, (
            f"Expected 2, got {self._repo.count_by_user('user1')}"
        )

    def test_get_by_id(self):
        """Should retrieve favorite by ID."""
        fav = self._repo.add_favorite("user1", "Civil Co")
        fetched = self._repo.get_by_id(fav.id)
        assert fetched is not None, "Should find by ID"
        assert fetched.contractor_name == "Civil Co", (
            f"Expected 'Civil Co', got '{fetched.contractor_name}'"
        )

    def test_delete_by_id(self):
        """Should delete favorite by ID."""
        fav = self._repo.add_favorite("user1", "Civil Co")
        assert self._repo.count() == 1, "Should have 1 favorite"

        deleted = self._repo.delete(fav.id)
        assert deleted is True, "Delete should return True"
        assert self._repo.count() == 0, "Should have 0 after delete"
