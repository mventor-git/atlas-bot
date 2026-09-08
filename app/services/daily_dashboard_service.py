"""Daily Dashboard Service for Labor-Report. (mventor-ticket-014)

Provides a comprehensive dashboard view of today's report status,
including Arabic-formatted date/time, contractor summary, submission
countdown, and context-sensitive quick action buttons.

Typical usage:
    service = DailyDashboardService(report_repo, config)
    dashboard = service.get_dashboard(telegram_user="user123")
    # dashboard.date -> "Ù¡Ù¥ / Ù Ù¨ / Ù¢Ù Ù¢Ù¦"
    # dashboard.buttons -> ["Open Draft", "Search"]
"""

from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta
from typing import Optional

from app.models.config import AppConfig
from app.models.database import Report, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.arabic_date_service import ArabicDateService
from app.utils.business_hours import get_now_in_timezone
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DashboardData:
    """Data for the daily dashboard display.

    All fields are pre-formatted for direct use by the Telegram handler.
    """

    date: str
    """Today's date in Arabic-Indic digits (e.g. 'Ù¡Ù¥ / Ù Ù¨ / Ù¢Ù Ù¢Ù¦')."""

    day: str
    """Arabic day name (e.g. 'Ø§Ù„Ø³Ø¨Øª')."""

    time: str
    """Current server time formatted as HH:MM."""

    report_status: str
    """Human-readable report status:
    'draft', 'final', 'locked', 'no_report', or 'not_created'."""

    contractor_count: int
    """Number of contractors in today's report."""

    total_workers: int
    """Sum of all workers across all items in today's report."""

    time_remaining: str
    """Time until the submission deadline (e.g. '3h 30m') or 'Closed'."""

    buttons: list[str] = field(default_factory=list)
    """Context-sensitive quick action button labels."""


class DailyDashboardService:
    """Builds a dashboard snapshot of today's report.

    Combines Arabic date/time formatting with report data from the
    database and config-driven deadline calculations.
    """

    def __init__(
        self,
        report_repository: ReportRepository,
        config: AppConfig,
    ) -> None:
        """Initialize the dashboard service.

        Args:
            report_repository: Repository for fetching today's report.
            config: Application configuration (for deadline and display settings).
        """
        self._repo = report_repository
        self._config = config

    def get_dashboard(
        self,
        telegram_user: Optional[str] = None,
        today: Optional[str] = None,
        now_time: Optional[datetime] = None,
    ) -> DashboardData:
        """Build dashboard data for the given date.

        Args:
            telegram_user: Who is requesting the dashboard (unused,
                           reserved for future per-user customization).
            today: Date string YYYY-MM-DD. If None, uses system today.
            now_time: Current datetime (injected for testability).
                      If None, uses the timezone configured in
                      ``config.timezone.name``.

        Returns:
            A fully populated DashboardData instance.
        """
        if today is None:
            today = date.today().isoformat()

        if now_time is None:
            # Use configured timezone
            tz_name = None
            if self._config is not None:
                try:
                    tz_config = getattr(self._config, "timezone", None)
                    if tz_config is not None:
                        tz_name = getattr(tz_config, "name", None)
                except Exception:
                    pass
            now_time = get_now_in_timezone(tz_name)

        # Parse the target date for Arabic formatting
        try:
            target_date = date.fromisoformat(today)
        except (ValueError, TypeError):
            target_date = now_time.date()

        # Arabic formatting
        arabic_date = ArabicDateService.get_arabic_date(target_date)
        day_name = ArabicDateService.get_arabic_day_name(target_date)
        time_str = now_time.strftime("%H:%M")

        # Fetch today's report
        report = self._repo.get_by_date(today)

        # Determine status, counts, and buttons
        if report is None:
            status = "not_created"
            contractor_count = 0
            total_workers = 0
        elif report.status == ReportStatus.NO_REPORT:
            status = "no_report"
            contractor_count = 0
            total_workers = 0
        else:
            status = report.status.value
            contractor_count = len(report.items) if report.items else 0
            if report.items:
                total_workers = sum(
                    (item.workers or 0) for item in report.items
                )
            else:
                total_workers = 0

        # Time remaining
        if self._config.dashboard.show_time_remaining:
            time_remaining = self._get_time_remaining(now_time)
        else:
            time_remaining = ""

        # Quick action buttons
        buttons = self._get_buttons(status)

        return DashboardData(
            date=arabic_date,
            day=day_name,
            time=time_str,
            report_status=status,
            contractor_count=contractor_count,
            total_workers=total_workers,
            time_remaining=time_remaining,
            buttons=buttons,
        )

    # ------------------------------------------------------------------
    # Time remaining
    # ------------------------------------------------------------------

    def _get_time_remaining(self, now: datetime) -> str:
        """Calculate time remaining until the submission deadline.

        Args:
            now: Current datetime.

        Returns:
            String like '3h 30m' if time remains, 'Closed' if deadline
            has passed, or empty string if show_time_remaining is False.
        """
        deadline_hour = self._config.dashboard.deadline_hour
        deadline_minute = self._config.dashboard.deadline_minute

        deadline_today = now.replace(
            hour=deadline_hour, minute=deadline_minute, second=0, microsecond=0,
        )

        if now >= deadline_today:
            return "Closed"

        diff = deadline_today - now
        total_minutes = int(diff.total_seconds() // 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60

        return f"{hours}h {minutes:02d}m"

    # ------------------------------------------------------------------
    # Quick action buttons
    # ------------------------------------------------------------------

    @staticmethod
    def _get_buttons(status: str) -> list[str]:
        """Determine context-sensitive quick action buttons.

        Args:
            status: The report status string.

        Returns:
            List of button labels appropriate for the current status.
        """
        if status in ("not_created", "no_report"):
            return ["Create Report", "Search"]
        elif status == ReportStatus.DRAFT.value:
            return ["Open Draft", "Search"]
        elif status == ReportStatus.FINAL.value:
            return ["View Report", "Search"]
        elif status == ReportStatus.LOCKED.value:
            return ["View Report", "Search"]
        else:
            return ["Search"]
