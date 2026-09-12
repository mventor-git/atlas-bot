"""Report Workflow Service for Labor-Report.

Manages the report lifecycle: Draft -> Final -> Locked -> Draft (admin unlock).
Enforces valid status transitions and logs all lifecycle events.
Optionally creates version snapshots on finalize.

Typical usage:
    service = ReportWorkflowService(report_repo, event_log_service, config)
    report = service.finalize_report(report, telegram_user="user123")
    report = service.lock_report(report, telegram_user="user123")
    report = service.unlock_report(report, telegram_user="admin", admin=True)
"""

from datetime import datetime, timedelta
from typing import Optional

from app.models.config import AppConfig
from app.models.database import Report, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.repositories.version_repository import VersionRepository
from app.services.event_log_service import EventLogService
from app.utils.exceptions import ReportLifecycleError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ReportWorkflowService:
    """Service for managing report lifecycle transitions.

    Encapsulates all business rules around status transitions
    (Draft -> Final -> Approved/Rejected -> Locked), including validation,
    timestamp management, event logging, and optional version snapshot
    creation.

    When a version_repository is provided, each finalize_report()
    call automatically creates a version snapshot and prunes old
    versions per lifecycle.max_versions config.
    """

    VALID_TRANSITIONS: dict[ReportStatus, set[ReportStatus]] = {
        ReportStatus.DRAFT: {ReportStatus.FINAL},
        ReportStatus.FINAL: {ReportStatus.APPROVED, ReportStatus.REJECTED},
        ReportStatus.APPROVED: {ReportStatus.LOCKED},
        ReportStatus.REJECTED: {ReportStatus.DRAFT},
        ReportStatus.LOCKED: {ReportStatus.DRAFT},
    }

    def __init__(
        self,
        report_repository: ReportRepository,
        event_log_service: EventLogService,
        config: AppConfig,
        version_repository: Optional[VersionRepository] = None,
    ) -> None:
        """Initialize the workflow service.

        Args:
            report_repository: Repository for report persistence.
            event_log_service: Service for lifecycle event logging.
            config: Application configuration (for lifecycle settings).
            version_repository: Optional repository for version snapshots.
                               If provided, versions are created on finalize.
        """
        self._repo = report_repository
        self._event_log = event_log_service
        self._config = config
        self._version_repo = version_repository

    def finalize_report(
        self, report: Report, telegram_user: str
    ) -> Report:
        """Transition a report from Draft to Final status.

        Args:
            report: The report to finalize (must be in DRAFT status).
            telegram_user: Who is performing the action.

        Returns:
            The updated report in FINAL status.

        Raises:
            ReportLifecycleError: If the report is not in DRAFT status.
        """
        self._validate_transition(report, ReportStatus.FINAL)

        now = datetime.now().isoformat()
        report.status = ReportStatus.FINAL
        report.finalized_at = now
        report.updated_at = now

        updated = self._repo.update(report, force=True)
        self._event_log.log_report_finalized(telegram_user, report)
        logger.info(
            "Report finalized: id=%s, date=%s, user=%s",
            report.id, report.date, telegram_user,
        )

        # Create version snapshot and prune old versions
        if self._version_repo is not None:
            try:
                self._version_repo.create_version(
                    updated, telegram_user=telegram_user,
                    change_summary="Report finalized",
                )
                max_ver = self._config.lifecycle.max_versions
                pruned = self._version_repo.prune_versions(updated.id, max_ver)
                if pruned:
                    logger.info(
                        "Pruned %d old version(s) for report id=%s",
                        pruned, updated.id,
                    )
            except Exception:
                logger.exception(
                    "Failed to create version snapshot for report id=%s",
                    updated.id,
                )

        return updated

    def lock_report(
        self, report: Report, telegram_user: str
    ) -> Report:
        """Transition a report from Approved to Locked status.

        Locking requires prior reviewer approval (Phase 3a, 027): FINAL
        reports must be approved first; FINAL -> LOCKED is refused.

        Args:
            report: The report to lock (must be APPROVED).
            telegram_user: Who is performing the action.

        Returns:
            The updated report in LOCKED status.

        Raises:
            ReportLifecycleError: If the report is not APPROVED.
        """
        self._validate_transition(report, ReportStatus.LOCKED)

        now = datetime.now().isoformat()
        report.status = ReportStatus.LOCKED
        report.locked_at = now
        report.locked_by = telegram_user
        report.updated_at = now

        updated = self._repo.update(report, force=True)
        self._event_log.log_report_locked(telegram_user, report)
        logger.info(
            "Report locked: id=%s, date=%s, user=%s",
            report.id, report.date, telegram_user,
        )
        return updated

    def approve_report(
        self, report: Report, telegram_user: str, note: str = ""
    ) -> Report:
        """Transition a report from Final to Approved (027).

        Args:
            report: The report to approve (must be FINAL).
            telegram_user: Reviewer (capability gate lives in handlers).
            note: Optional reviewer note (kept on the record).
        """
        self._validate_transition(report, ReportStatus.APPROVED)

        now = datetime.now().isoformat()
        report.status = ReportStatus.APPROVED
        report.approved_by = telegram_user
        report.approved_at = now
        report.updated_at = now

        updated = self._repo.update(report, force=True)
        self._event_log.log_report_approved(telegram_user, report, note=note)
        logger.info(
            "Report approved: id=%s, date=%s, user=%s",
            report.id, report.date, telegram_user,
        )
        return updated

    def reject_report(
        self, report: Report, telegram_user: str, note: str
    ) -> Report:
        """Transition a report from Final to Rejected (027, note required)."""
        self._validate_transition(report, ReportStatus.REJECTED)
        if not (note or "").strip():
            raise ReportLifecycleError(
                "Rejection requires a note.",
                current_status=report.status.value,
                target_status=ReportStatus.REJECTED.value,
            )

        now = datetime.now().isoformat()
        report.status = ReportStatus.REJECTED
        report.rejected_by = telegram_user
        report.reject_note = note.strip()
        report.updated_at = now

        updated = self._repo.update(report, force=True)
        self._event_log.log_report_rejected(telegram_user, report)
        logger.info(
            "Report rejected: id=%s, date=%s, user=%s",
            report.id, report.date, telegram_user,
        )
        return updated

    def resubmit_report(self, report: Report, telegram_user: str) -> Report:
        """Transition a report from Rejected back to Draft (027)."""
        self._validate_transition(report, ReportStatus.DRAFT)

        report.status = ReportStatus.DRAFT
        report.updated_at = datetime.now().isoformat()

        updated = self._repo.update(report, force=True)
        self._event_log.log_report_resubmitted(telegram_user, report)
        logger.info(
            "Report resubmitted: id=%s, date=%s, user=%s",
            report.id, report.date, telegram_user,
        )
        return updated

    def unlock_report(
        self,
        report: Report,
        telegram_user: str,
        *,
        admin: bool = False,
    ) -> Report:
        """Transition a report from Locked back to Draft status.

        Only designated admin users may unlock a report.

        Args:
            report: The report to unlock (must be in LOCKED status).
            telegram_user: Who is performing the action.
            admin: Must be True; non-admin unlock is rejected.

        Returns:
            The updated report in DRAFT status.

        Raises:
            ReportLifecycleError: If the report is not LOCKED,
                or if admin is False.
        """
        if not admin:
            raise ReportLifecycleError(
                "Only admins can unlock a report.",
                current_status=report.status.value if report.status else None,
                target_status=ReportStatus.DRAFT.value,
            )

        self._validate_transition(report, ReportStatus.DRAFT)

        now = datetime.now().isoformat()
        report.status = ReportStatus.DRAFT
        report.updated_at = now
        # Keep locked_at/locked_by for audit trail

        updated = self._repo.update(report, force=True)
        self._event_log.log_report_unlocked(telegram_user, report)
        logger.info(
            "Report unlocked: id=%s, date=%s, user=%s",
            report.id, report.date, telegram_user,
        )
        return updated

    def auto_finalize_drafts(
        self,
        current_hour: int | None = None,
        current_minute: int | None = None,
        telegram_user: str = "system",
        today_str: str | None = None,
        site_id: str | None = None,
    ) -> int:
        """Auto-finalize draft reports for today after the configured deadline.

        When the current time matches or exceeds ``lifecycle.auto_finalize_hour`` /
        ``lifecycle.auto_finalize_minute``, all DRAFT reports for today are
        automatically transitioned to FINAL status.

        This uses ``get_by_date()`` (which loads report items) rather than
        ``get_all()`` (which omits items for performance).

        Args:
            current_hour: Override hour (for testing). If None, uses system time.
            current_minute: Override minute (for testing). If None, uses system time.
            telegram_user: Who to attribute the auto-finalize action to
                           (default: "system").

        Returns:
            Number of reports that were auto-finalized.
        """
        auto_hour = self._config.lifecycle.auto_finalize_hour
        if auto_hour < 0:
            return 0

        # Determine current time
        now = datetime.now()
        hour = current_hour if current_hour is not None else now.hour
        minute = current_minute if current_minute is not None else now.minute

        target_total = auto_hour * 60 + self._config.lifecycle.auto_finalize_minute
        current_total = hour * 60 + minute

        # Only trigger if current time is at or past the deadline
        if current_total < target_total:
            return 0

        if today_str is None:
            today_str = now.strftime("%Y-%m-%d")
        finalized_count = 0

        # Use get_by_date() which loads items (get_all() skips items for performance)
        today_report = self._repo.get_by_date(today_str, site_id=site_id)
        if today_report is None:
            return 0

        if today_report.status != ReportStatus.DRAFT:
            return 0
        if not today_report.items:
            return 0

        try:
            self.finalize_report(today_report, telegram_user)
            finalized_count = 1
        except Exception:
            logger.exception(
                "Auto-finalize failed for report id=%s, date=%s",
                today_report.id, today_report.date,
            )

        if finalized_count:
            logger.info(
                "Auto-finalized %d draft report(s) for %s",
                finalized_count, today_str,
            )
        return finalized_count

    def auto_lock_reports(self, telegram_user: str = "system",
                            site_id: str | None = None) -> int:
        """Automatically lock APPROVED reports past the auto-lock threshold.

        Only APPROVED reports lock (Phase 3a: FINAL must be reviewed first).
        ``site_id`` scopes the sweep; None keeps the deployment default so
        Phase 6 can loop sites explicitly. Scans reports whose finalized_at
        timestamp is older than ``lifecycle.auto_lock_hours``.

        Args:
            telegram_user: Who to attribute the auto-lock action to
                           (default: "system").

        Returns:
            Number of reports that were auto-locked.
        """
        auto_hours = self._config.lifecycle.auto_lock_hours
        if auto_hours <= 0:
            return 0

        threshold = datetime.now() - timedelta(hours=auto_hours)
        locked_count = 0

        all_reports = self._repo.get_all(site_id=site_id)
        for report in all_reports:
            if report.status != ReportStatus.APPROVED:
                continue
            if not report.finalized_at:
                continue
            try:
                finalized_time = datetime.fromisoformat(report.finalized_at)
            except (ValueError, TypeError):
                continue

            if finalized_time < threshold:
                try:
                    self.lock_report(report, telegram_user)
                    locked_count += 1
                except Exception:
                    logger.exception(
                        "Auto-lock failed for report id=%s", report.id
                    )

        if locked_count:
            logger.info("Auto-locked %d report(s)", locked_count)
        return locked_count

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_transition(
        self, report: Report, target_status: ReportStatus
    ) -> None:
        """Validate that the status transition is allowed.

        Args:
            report: The current report.
            target_status: The desired target status.

        Raises:
            ReportLifecycleError: If the transition is invalid.
        """
        if report.status is None:
            raise ReportLifecycleError(
                "Report has no status.",
                current_status=None,
                target_status=target_status.value,
            )

        allowed = self.VALID_TRANSITIONS.get(report.status, set())
        if target_status not in allowed:
            raise ReportLifecycleError(
                f"Cannot transition report from '{report.status.value}' "
                f"to '{target_status.value}'. "
                f"Allowed transitions from '{report.status.value}': "
                f"{' → '.join(s.value for s in allowed) if allowed else 'none'}.",
                current_status=report.status.value,
                target_status=target_status.value,
            )
