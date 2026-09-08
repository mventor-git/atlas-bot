"""
Tests for ContractorRepository. (mventor-ticket-036)

Covers:
- Add contractor with name and type
- Add duplicate contractor returns None
- Add empty name returns None
- Get by exact name (case-insensitive)
- Search by partial name
- Get all contractors
- Delete contractor
- Count contractors
- Contractor exists check
"""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.repositories.contractor_repository import ContractorRepository


class TestContractorRepository:
    """Test suite for ContractorRepository."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_contractors.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return ContractorRepository(db_manager)

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------

    def test_add_contractor(self, repo: ContractorRepository):
        """Should add a contractor with name and type."""
        result = repo.add("Civil Co", "admin1", "Civil")
        assert result is not None
        assert result.name == "Civil Co"
        assert result.type == "Civil"

    def test_add_contractor_without_type(self, repo: ContractorRepository):
        """Should add a contractor without a type."""
        result = repo.add("Electric Inc", "admin1")
        assert result is not None
        assert result.name == "Electric Inc"
        assert result.type is None

    def test_add_duplicate_returns_none(self, repo: ContractorRepository):
        """Should return None when adding a duplicate name."""
        repo.add("Civil Co", "admin1")
        result = repo.add("Civil Co", "admin2")
        assert result is None

    def test_add_duplicate_case_insensitive(self, repo: ContractorRepository):
        """Should detect duplicates case-insensitively."""
        repo.add("Civil Co", "admin1")
        result = repo.add("civil co", "admin2")
        assert result is None

    def test_add_empty_name_returns_none(self, repo: ContractorRepository):
        """Should return None for empty name."""
        assert repo.add("", "admin1") is None
        assert repo.add("   ", "admin1") is None

    # ------------------------------------------------------------------
    # Get by name
    # ------------------------------------------------------------------

    def test_get_by_name(self, repo: ContractorRepository):
        """Should find contractor by exact name."""
        repo.add("Civil Co", "admin1", "Civil")
        contractor = repo.get_by_name("Civil Co")
        assert contractor is not None
        assert contractor.name == "Civil Co"
        assert contractor.type == "Civil"

    def test_get_by_name_case_insensitive(self, repo: ContractorRepository):
        """Should find contractor case-insensitively."""
        repo.add("Civil Co", "admin1")
        assert repo.get_by_name("civil co") is not None
        assert repo.get_by_name("CIVIL CO") is not None

    def test_get_by_name_not_found(self, repo: ContractorRepository):
        """Should return None for nonexistent contractor."""
        assert repo.get_by_name("Nonexistent") is None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def test_search_partial(self, repo: ContractorRepository):
        """Should find contractors by partial name."""
        repo.add("Civil Co", "admin1")
        repo.add("Civil Engineering", "admin1")
        repo.add("Electric Inc", "admin1")

        results = repo.search("civil")
        assert len(results) == 2
        names = {c.name for c in results}
        assert "Civil Co" in names
        assert "Civil Engineering" in names

    def test_search_empty_query(self, repo: ContractorRepository):
        """Should return empty list for empty query."""
        repo.add("Civil Co", "admin1")
        assert repo.search("") == []
        assert repo.search("   ") == []

    def test_search_no_match(self, repo: ContractorRepository):
        """Should return empty list when nothing matches."""
        repo.add("Civil Co", "admin1")
        assert repo.search("zzzzz") == []

    def test_search_respects_max_results(self, repo: ContractorRepository):
        """Should limit search results."""
        for i in range(20):
            repo.add(f"Test Contractor {i}", "admin1")
        results = repo.search("Test", max_results=5)
        assert len(results) == 5

    # ------------------------------------------------------------------
    # Get all
    # ------------------------------------------------------------------

    def test_get_all(self, repo: ContractorRepository):
        """Should return all contractors sorted by name."""
        repo.add("Electric Inc", "admin1")
        repo.add("Civil Co", "admin1")
        repo.add("Mechanical Co", "admin1")

        all_c = repo.get_all()
        assert len(all_c) == 3
        # Should be sorted by name
        assert all_c[0].name == "Civil Co"
        assert all_c[1].name == "Electric Inc"
        assert all_c[2].name == "Mechanical Co"

    def test_get_all_empty(self, repo: ContractorRepository):
        """Should return empty list when no contractors exist."""
        assert repo.get_all() == []

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def test_delete(self, repo: ContractorRepository):
        """Should delete a contractor by name."""
        repo.add("Civil Co", "admin1")
        assert repo.delete("Civil Co") is True
        assert repo.get_by_name("Civil Co") is None

    def test_delete_nonexistent(self, repo: ContractorRepository):
        """Should return False for nonexistent contractor."""
        assert repo.delete("Nonexistent") is False

    def test_delete_case_insensitive(self, repo: ContractorRepository):
        """Should delete case-insensitively."""
        repo.add("Civil Co", "admin1")
        assert repo.delete("civil co") is True

    # ------------------------------------------------------------------
    # Count
    # ------------------------------------------------------------------

    def test_count(self, repo: ContractorRepository):
        """Should return correct count."""
        assert repo.count() == 0
        repo.add("Civil Co", "admin1")
        assert repo.count() == 1
        repo.add("Electric Inc", "admin1")
        assert repo.count() == 2

    # ------------------------------------------------------------------
    # Exists
    # ------------------------------------------------------------------

    def test_exists(self, repo: ContractorRepository):
        """Should check if contractor exists."""
        assert repo.exists("Civil Co") is False
        repo.add("Civil Co", "admin1")
        assert repo.exists("Civil Co") is True
        assert repo.exists("civil co") is True
        assert repo.exists("Nonexistent") is False
