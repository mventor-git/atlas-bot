"""One Click Yesterday Service for Labor-Report. (mventor-ticket-010)

Creates today's draft report by copying yesterday's report data.
Yesterday's report remains unchanged. The returned draft is an
in-memory object — the handler can let the user edit it before
persisting via AutoSaveService.

Typical usage:
    service = OneClickYesterdayService(report_repo)
    draft = service.copy_yesterday(
        yesterday_date="2026-07-10",
        today_date="2026-07-11",
        today_day="السبت",
        telegram_user="user123",
    )
    if draft is None:
        # No report for yesterday — start fresh
    else:
        # User edits draft, then auto-save persists it
"""

from typing import Optional

from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class OneClickYesterdayService:
    """Service for copying yesterday's report as today's draft.

    Loads yesterday's report from the database, creates a new in-memory
    draft for today with all items copied, and sets ``source_date``
    to yesterday's date for source tracking.
    """

    def __init__(self, report_repository: ReportRepository) -> None:
        """Initialize the service.

        Args:
            report_repository: Repository for fetching yesterday's report.
        """
        self._repo = report_repository

    def copy_yesterday(
        self,
        yesterday_date: str,
        today_date: str,
        today_day: str,
        telegram_user: str,
    ) -> Optional[Report]:
        """Copy yesterday's report as today's draft.

        Returns a new draft ``Report`` for ``today_date`` with all items
        copied from yesterday's report, or ``None`` if yesterday has no
        report or is a ``no_report`` day.

        Yesterday's report in the database is **not** modified.

        Args:
            yesterday_date: Yesterday's date string (YYYY-MM-DD).
            today_date: Today's date string (YYYY-MM-DD).
            today_day: Today's Arabic day name (e.g. 'السبت').
            telegram_user: Who is performing this action.

        Returns:
            A new draft Report for today (not persisted), or None if
            yesterday has no copyable report.
        """
        yesterday_report = self._repo.get_by_date(yesterday_date)

        if yesterday_report is None:
            logger.info(
                "No yesterday report for %s — cannot copy", yesterday_date
            )
            return None

        if yesterday_report.status == ReportStatus.NO_REPORT:
            logger.info(
                "Yesterday (%s) is a no-report day — cannot copy",
                yesterday_date,
            )
            return None

        # Build today's draft by copying all items
        today_draft = Report(
            date=today_date,
            day=today_day,
            status=ReportStatus.DRAFT,
            telegram_user=telegram_user,
            source_date=yesterday_date,
        )

        for item in yesterday_report.items:
            today_draft.add_item(
                ReportItem(
                    contractor=item.contractor,
                    type=item.type,
                    zone=item.zone,
                    workers=item.workers,
                    details=item.details,
                    contractor_code=item.contractor_code,
                )
            )

        logger.info(
            "Copied yesterday report (%s, %d items) as draft for %s (user=%s)",
            yesterday_date, len(today_draft.items), today_date, telegram_user,
        )
        return today_draft
