"""
Tests for AddContractorService. (mventor-ticket-036)

Covers:
- Add contractor successfully
- Add duplicate rejected
- Empty name rejected
- Name too long rejected
- Audit logging integrates correctly
- Without audit service still works
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.database.manager import DatabaseManager
from app.repositories.contractor_repository import ContractorRepository
from app.services.add_contractor_service import AddContractorService
from app.services.audit_service import AuditService


class TestAddContractorService:
    """Test suite for AddContractorService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_add_contractor.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def contractor_repo(self, db_manager: DatabaseManager):
        return ContractorRepository(db_manager)

    @pytest.fixture
    def audit_service(self, db_manager: DatabaseManager):
        return AuditService(db_manager)

    @pytest.fixture
    def service(self, contractor_repo: ContractorRepository):
        return AddContractorService(contractor_repo)

    @pytest.fixture
    def service_with_audit(self, contractor_repo: ContractorRepository, audit_service: AuditService):
        return AddContractorService(contractor_repo, audit_service)

    # ------------------------------------------------------------------
    # Successful add
    # ------------------------------------------------------------------

    def test_add_contractor_success(self, service: AddContractorService):
        """Should add a contractor successfully."""
        success, message = service.add_contractor("Civil Co", "admin1", "admin", "Civil")
        assert success is True
        assert "added successfully" in message

    def test_add_contractor_without_type(self, service: AddContractorService):
        """Should add contractor without type."""
        success, message = service.add_contractor("Electric Inc", "admin1", "admin")
        assert success is True

    # ------------------------------------------------------------------
    # Duplicate
    # ------------------------------------------------------------------

    def test_add_duplicate_rejected(self, service: AddContractorService):
        """Should reject duplicate contractor."""
        service.add_contractor("Civil Co", "admin1", "admin")
        success, message = service.add_contractor("Civil Co", "admin2", "admin")
        assert success is False
        assert "already exists" in message

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def test_empty_name_rejected(self, service: AddContractorService):
        """Should reject empty name."""
        success, message = service.add_contractor("", "admin1", "admin")
        assert success is False
        assert "empty" in message.lower()

    def test_name_too_long_rejected(self, service: AddContractorService):
        """Should reject overly long name."""
        long_name = "A" * 201
        success, message = service.add_contractor(long_name, "admin1", "admin")
        assert success is False
        assert "too long" in message.lower()

    # ------------------------------------------------------------------
    # Audit integration
    # ------------------------------------------------------------------

    def test_audit_logged_on_add(self, service_with_audit: AddContractorService, db_manager: DatabaseManager):
        """Should log contractor addition to audit."""
        service_with_audit.add_contractor("Audit Test Co", "admin1", "superadmin", "Civil")

        # Check audit log was created
        from app.repositories.audit_repository import AuditRepository
        audit_repo = AuditRepository(db_manager)
        entries = audit_repo.get_by_user("admin1")
        assert len(entries) >= 1
        # Find the contractor add entry
        add_entries = [e for e in entries if e.contractor_name == "Audit Test Co"]
        assert len(add_entries) == 1
        assert add_entries[0].action == "added"
        assert add_entries[0].user_role == "superadmin"

    def test_works_without_audit_service(self, service: AddContractorService):
        """Should work without audit service."""
        success, message = service.add_contractor("No Audit Co", "admin1", "admin")
        assert success is True

    # ------------------------------------------------------------------
    # Simple interface (for testing)
    # ------------------------------------------------------------------

    def test_add_contractor_simple(self, service: AddContractorService):
        """Should add via simple interface."""
        success, message = service.add_contractor_simple("Simple Co", "Testing")
        assert success is True
        assert service._contractor_repo.get_by_name("Simple Co") is not None
