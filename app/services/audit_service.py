"""
Audit Service for Labor-Report.

Provides a clean API for logging user activity and querying audit data.
Wraps AuditRepository with common use-case methods.
"""

from typing import Optional

from app.database.manager import DatabaseManager
from app.models.audit import UserActivityLog
from app.models.database import Report, ReportItem
from app.repositories.audit_repository import AuditRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AuditService:
    """Service for logging and querying user activity.

    Tracks:
    - What values users add to reports
    - When values are reverted
    - When users view/export reports
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        """Initialize the audit service.

        Args:
            db_manager: Database manager instance.
        """
        self._repo = AuditRepository(db_manager)

    # --- Logging ---

    def log_added(
        self,
        telegram_user: str,
        user_role: str,
        report: Report,
        item: ReportItem,
    ) -> UserActivityLog:
        """Log a contractor value being added to a report.

        Args:
            telegram_user: User who added the value.
            user_role: User's role at the time.
            report: The report being edited.
            item: The report item being added.

        Returns:
            The logged entry (has .id for revert reference).
        """
        return self._repo.log_added(
            telegram_user=telegram_user,
            user_role=user_role,
            report_date=report.date,
            report_status=report.status.value if report.status else "draft",
            contractor_name=item.contractor,
            workers=item.workers,
            zone=item.zone,
            details=item.details,
        )

    def log_reverted(
        self,
        telegram_user: str,
        user_role: str,
        report: Report,
        original_entry_id: Optional[int] = None,
        contractor_name: Optional[str] = None,
    ) -> UserActivityLog:
        """Log a value being reverted.

        Args:
            telegram_user: User who performed the revert.
            user_role: User's role at the time.
            report: The affected report.
            original_entry_id: ID of the original 'added' entry.
            contractor_name: Name of the reverted contractor.

        Returns:
            The logged entry.
        """
        return self._repo.log_reverted(
            telegram_user=telegram_user,
            user_role=user_role,
            report_date=report.date,
            report_status=report.status.value if report.status else "draft",
            original_entry_id=original_entry_id or 0,
            contractor_name=contractor_name,
        )

    def log_viewed(
        self,
        telegram_user: str,
        user_role: str,
        report: Report,
    ) -> UserActivityLog:
        """Log a user viewing a report.

        Args:
            telegram_user: User who viewed.
            user_role: User's role at the time.
            report: The viewed report.

        Returns:
            The logged entry.
        """
        return self._repo.log_viewed(
            telegram_user=telegram_user,
            user_role=user_role,
            report_date=report.date,
            report_status=report.status.value if report.status else "unknown",
        )

    def log_locked(
        self,
        telegram_user: str,
        user_role: str,
        report: Report,
        details: Optional[str] = None,
    ) -> UserActivityLog:
        """Log a report being locked.

        Args:
            telegram_user: User who locked the report.
            user_role: User's role at the time.
            report: The locked report.
            details: Optional details (e.g. 'auto-lock').

        Returns:
            The logged entry.
        """
        return self._repo.log_locked(
            telegram_user=telegram_user,
            user_role=user_role,
            report_date=report.date,
            report_status=report.status.value if report.status else "unknown",
            details=details,
        )

    def log_unlocked(
        self,
        telegram_user: str,
        user_role: str,
        report: Report,
        details: Optional[str] = None,
    ) -> UserActivityLog:
        """Log a report being unlocked.

        Args:
            telegram_user: User who unlocked the report.
            user_role: User's role at the time.
            report: The unlocked report.
            details: Optional details.

        Returns:
            The logged entry.
        """
        return self._repo.log_unlocked(
            telegram_user=telegram_user,
            user_role=user_role,
            report_date=report.date,
            report_status=report.status.value if report.status else "unknown",
            details=details,
        )

    def log_exported(
        self,
        telegram_user: str,
        user_role: str,
        report: Report,
    ) -> UserActivityLog:
        """Log a user exporting/downloading a PDF.

        Args:
            telegram_user: User who exported.
            user_role: User's role at the time.
            report: The exported report.

        Returns:
            The logged entry.
        """
        return self._repo.log_exported(
            telegram_user=telegram_user,
            user_role=user_role,
            report_date=report.date,
            report_status=report.status.value if report.status else "unknown",
        )

    # --- Queries ---

    def get_user_activity(self, telegram_user: str, limit: int = 100) -> list[UserActivityLog]:
        """Get all activity for a specific user.

        Args:
            telegram_user: Telegram user ID.
            limit: Maximum entries.

        Returns:
            List of UserActivityLog entries.
        """
        return self._repo.get_by_user(telegram_user, limit)

    def get_added_values_by_user(self, telegram_user: str) -> list[UserActivityLog]:
        """Get all 'added' entries by a user that haven't been reverted.

        Args:
            telegram_user: Telegram user ID.

        Returns:
            List of 'added' UserActivityLog entries that were NOT reverted.
        """
        return self._repo.get_added_values_by_user(telegram_user)

    def get_views_by_user(self, telegram_user: str) -> list[UserActivityLog]:
        """Get all 'viewed' entries by a user.

        Args:
            telegram_user: Telegram user ID.

        Returns:
            List of 'viewed' UserActivityLog entries.
        """
        return self._repo.get_views_by_user(telegram_user)

    def get_recent_activity(self, limit: int = 50) -> list[UserActivityLog]:
        """Get the most recent activity across all users.

        Args:
            limit: Maximum entries.

        Returns:
            List of recent UserActivityLog entries.
        """
        return self._repo.get_recent_by_all_users(limit)

    def get_distinct_users(self) -> list[dict]:
        """Get distinct users who have activity logs.

        Returns:
            List of dicts with 'telegram_user', 'user_role', 'last_active'.
        """
        return self._repo.get_distinct_users()
