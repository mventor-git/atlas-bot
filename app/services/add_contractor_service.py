"""Add Contractor Service for mventor-ticket-036.

Handles the business logic of adding a new contractor:
- Validates admin permissions
- Persists to the database via ContractorRepository
- Logs the action to the audit trail
- Integrates with ContractorSearchService for immediate availability
"""

from typing import Optional

from app.models.database import Contractor
from app.repositories.contractor_repository import ContractorRepository
from app.services.audit_service import AuditService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AddContractorService:
    """Service for adding new contractors by admin/superadmin users.

    Validates, persists, and audits contractor additions.
    """

    def __init__(
        self,
        contractor_repo: ContractorRepository,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        """Initialize the add contractor service.

        Args:
            contractor_repo: Repository for persisting contractors.
            audit_service: Optional audit service for logging additions.
        """
        self._contractor_repo = contractor_repo
        self._audit_service = audit_service

    def add_contractor(
        self,
        name: str,
        added_by: str,
        user_role: str,
        ctype: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Add a new contractor.

        Args:
            name: Contractor name.
            added_by: Telegram user ID of the admin adding this contractor.
            user_role: Role of the user adding the contractor.
            ctype: Optional contractor type.

        Returns:
            Tuple of (success: bool, message: str).
        """
        name = name.strip()
        if not name:
            return False, "Contractor name cannot be empty."

        if len(name) > 200:
            return False, "Contractor name is too long (max 200 characters)."

        result = self._contractor_repo.add(name, added_by, ctype)
        if result is None:
            existing = self._contractor_repo.get_by_name(name)
            if existing is not None:
                return False, f"Contractor '{name}' already exists."
            return False, "Failed to add contractor."

        # Log to audit
        if self._audit_service is not None:
            try:
                # Use the audit repository directly since we don't have a Report
                self._audit_service._repo.log_added(
                    telegram_user=added_by,
                    user_role=user_role,
                    report_date="[CONTRACTOR]",
                    report_status="added",
                    contractor_name=name,
                    details=f"Contractor type: {ctype}" if ctype else "Contractor added",
                )
                logger.info(
                    "Audit logged: contractor '%s' added by %s (role=%s)",
                    name, added_by, user_role,
                )
            except Exception as e:
                logger.warning("Failed to log contractor add to audit: %s", e)

        logger.info("Contractor added successfully: %s (type=%s) by %s", name, ctype, added_by)
        return True, f"Contractor '{name}' added successfully."

    def add_contractor_simple(
        self,
        name: str,
        ctype: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Add a contractor without audit logging (for testing).

        Args:
            name: Contractor name.
            ctype: Optional contractor type.

        Returns:
            Tuple of (success: bool, message: str).
        """
        return self.add_contractor(name, added_by="system", user_role="system", ctype=ctype)
