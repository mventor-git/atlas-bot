"""
Tests for RecentContractorRepository.

Tests cover:
- Recording contractor usage
- Getting recently used contractors per user
- Usage count incrementing
- Per-user isolation
- Clearing records
- Batch recording
- Edge cases (empty results, missing users)
"""

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from app.database.manager import DatabaseManager
from app.models.database import RecentContractor
from app.repositories.recent_contractor_repository import RecentContractorRepository


class TestRecentContractorRepository:
    """Test suite for RecentContractorRepository."""

    @pytest.fixture
    def temp_dir(self) -> Generator[Path, None, None]:
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            yield Path(tmp_dir)

    @pytest.fixture
    def db_manager(self, temp_dir: Path) -> Generator[DatabaseManager, None, None]:
        """Create a DatabaseManager with a temporary database."""
        db = DatabaseManager(str(temp_dir / "test.db"))
        yield db
        db.close()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager) -> RecentContractorRepository:
        """Create a RecentContractorRepository instance."""
        return RecentContractorRepository(db_manager)

    # --- Recording Usage ---

    def test_record_usage_creates_entry(self, repo: RecentContractorRepository):
        """Should create a new entry when contractor is first used."""
        repo.record_usage("user1", "Civil Contractor")
        recent = repo.get_recent_for_user("user1")
        assert len(recent) == 1
        assert recent[0].contractor_name == "Civil Contractor"
        assert recent[0].telegram_user == "user1"
        assert recent[0].use_count == 1

    def test_record_usage_increments_count(self, repo: RecentContractorRepository):
        """Should increment use count when same contractor is reused."""
        repo.record_usage("user1", "Civil Contractor")
        repo.record_usage("user1", "Civil Contractor")
        repo.record_usage("user1", "Civil Contractor")
        recent = repo.get_recent_for_user("user1")
        assert len(recent) == 1
        assert recent[0].use_count == 3

    def test_record_usage_updates_timestamp(self, repo: RecentContractorRepository):
        """Should update the last_used timestamp on reuse."""
        import time
        repo.record_usage("user1", "Civil Contractor")
        first_ts = repo.get_recent_for_user("user1")[0].last_used

        time.sleep(0.01)  # Ensure time advances
        repo.record_usage("user1", "Civil Contractor")
        second_ts = repo.get_recent_for_user("user1")[0].last_used

        assert second_ts > first_ts

    # --- Per-User Isolation ---

    def test_per_user_isolation(self, repo: RecentContractorRepository):
        """Different users should have separate recent lists."""
        repo.record_usage("user1", "Civil Contractor")
        repo.record_usage("user2", "Electrical Contractor")

        user1_recent = repo.get_recent_for_user("user1")
        user2_recent = repo.get_recent_for_user("user2")

        assert len(user1_recent) == 1
        assert user1_recent[0].contractor_name == "Civil Contractor"
        assert user2_recent[0].contractor_name == "Electrical Contractor"

    # --- Ordering ---

    def test_most_recent_first(self, repo: RecentContractorRepository):
        """Should return most recently used contractor first."""
        repo.record_usage("user1", "First")
        import time
        time.sleep(0.01)
        repo.record_usage("user1", "Second")
        time.sleep(0.01)
        repo.record_usage("user1", "Third")

        recent = repo.get_recent_for_user("user1")
        assert recent[0].contractor_name == "Third"
        assert recent[1].contractor_name == "Second"
        assert recent[2].contractor_name == "First"

    def test_reuse_moves_to_top(self, repo: RecentContractorRepository):
        """Reusing a contractor should move it to the top."""
        repo.record_usage("user1", "A")
        repo.record_usage("user1", "B")
        repo.record_usage("user1", "C")
        import time
        time.sleep(0.01)
        repo.record_usage("user1", "A")  # Reuse A

        recent = repo.get_recent_for_user("user1")
        assert recent[0].contractor_name == "A"

    # --- Limit ---

    def test_respects_limit(self, repo: RecentContractorRepository):
        """Should respect the limit parameter."""
        for i in range(20):
            repo.record_usage("user1", f"Contractor {i}")

        recent = repo.get_recent_for_user("user1", limit=5)
        assert len(recent) == 5

        full = repo.get_recent_for_user("user1", limit=50)
        assert len(full) == 20

    # --- Get Recent Names ---

    def test_get_recent_names(self, repo: RecentContractorRepository):
        """Should return just the names."""
        repo.record_usage("user1", "Alpha")
        repo.record_usage("user1", "Beta")
        names = repo.get_recent_names("user1", limit=5)
        assert names == ["Beta", "Alpha"]

    def test_get_recent_names_empty(self, repo: RecentContractorRepository):
        """Should return empty list for user with no history."""
        names = repo.get_recent_names("unknown_user")
        assert names == []

    # --- Batch Recording ---

    def test_record_usage_batch(self, repo: RecentContractorRepository):
        """Should record multiple contractors at once."""
        repo.record_usage_batch("user1", ["A", "B", "C"])
        recent = repo.get_recent_for_user("user1")
        assert len(recent) == 3

    def test_record_usage_batch_with_duplicates(self, repo: RecentContractorRepository):
        """Batch recording duplicates should increment count."""
        repo.record_usage_batch("user1", ["A", "A", "A", "B"])
        recent = repo.get_recent_for_user("user1")
        name_map = {r.contractor_name: r.use_count for r in recent}
        assert name_map["A"] == 3
        assert name_map["B"] == 1

    # --- Clearing ---

    def test_clear_for_user(self, repo: RecentContractorRepository):
        """Should clear all records for a user."""
        repo.record_usage("user1", "A")
        repo.record_usage("user1", "B")
        repo.record_usage("user2", "C")

        deleted = repo.clear_for_user("user1")
        assert deleted == 2

        user1_recent = repo.get_recent_for_user("user1")
        assert len(user1_recent) == 0

        # User2 should be unaffected
        assert len(repo.get_recent_for_user("user2")) == 1

    def test_clear_for_unknown_user(self, repo: RecentContractorRepository):
        """Should return 0 when clearing nonexistent user."""
        deleted = repo.clear_for_user("nonexistent")
        assert deleted == 0

    # --- Edge Cases ---

    def test_empty_history(self, repo: RecentContractorRepository):
        """Should return empty list for user with no history."""
        recent = repo.get_recent_for_user("new_user")
        assert recent == []

    def test_single_contractor_multiple_users(self, repo: RecentContractorRepository):
        """Should track same contractor for different users."""
        repo.record_usage("user1", "SameCo")
        repo.record_usage("user2", "SameCo")

        assert repo.get_recent_for_user("user1")[0].contractor_name == "SameCo"
        assert repo.get_recent_for_user("user2")[0].contractor_name == "SameCo"

    def test_count_total(self, repo: RecentContractorRepository):
        """Should return total count across all users."""
        assert repo.count() == 0
        repo.record_usage("user1", "A")
        assert repo.count() == 1
        repo.record_usage("user2", "B")
        assert repo.count() == 2

    def test_get_by_id(self, repo: RecentContractorRepository):
        """Should retrieve record by ID."""
        repo.record_usage("user1", "Test Contractor")
        recent = repo.get_recent_for_user("user1")
        record_id = recent[0].id
        assert record_id is not None

        retrieved = repo.get_by_id(record_id)
        assert retrieved is not None
        assert retrieved.contractor_name == "Test Contractor"

    def test_get_by_id_nonexistent(self, repo: RecentContractorRepository):
        """Should return None for nonexistent ID."""
        assert repo.get_by_id(99999) is None

    def test_get_all(self, repo: RecentContractorRepository):
        """Should return all records across all users."""
        repo.record_usage("user1", "A")
        repo.record_usage("user2", "B")
        all_records = repo.get_all()
        assert len(all_records) == 2

    def test_add_and_update(self, repo: RecentContractorRepository):
        """Should support add and update operations."""
        entity = RecentContractor(
            contractor_name="NewCo",
            telegram_user="user1",
            last_used="2026-07-11T10:00:00",
            use_count=1,
        )
        added = repo.add(entity)
        assert added.id is not None

        added.use_count = 5
        updated = repo.update(added)
        assert updated.use_count == 5

    def test_delete(self, repo: RecentContractorRepository):
        """Should delete a record by ID."""
        repo.record_usage("user1", "DeleteMe")
        recent = repo.get_recent_for_user("user1")
        record_id = recent[0].id

        deleted = repo.delete(record_id)
        assert deleted is True
        assert len(repo.get_recent_for_user("user1")) == 0

    def test_delete_nonexistent(self, repo: RecentContractorRepository):
        """Should return False when deleting nonexistent ID."""
        deleted = repo.delete(99999)
        assert deleted is False
