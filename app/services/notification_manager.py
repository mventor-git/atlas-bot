"""
Notification Manager — Automated reminder service for Labor-Report. (NEW)

Sends scheduled Telegram notifications to users at configurable times:
  - 9 AM  : "Workday started — create your report?"
  - 11 AM : "Reminder — report not yet created"
  - 2 PM  : "Final reminder — deadline approaching"
  - 5 PM  : Auto-creates a no_report entry using empty-day.xlsx template

All times are evaluated in the configured timezone (config.yaml → timezone.name).
The manager runs as a background asyncio task inside the bot application.
"""

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from telegram.ext import Application

from app.database.manager import DatabaseManager
from app.models.config import AppConfig
from app.models.database import Report, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.arabic_date_service import ArabicDateService
from app.services.working_calendar import WorkingCalendar
from app.utils.exceptions import DatabaseError

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)


# ─── Notification messages ─────────────────────────────────────────


MORNING_REMINDER = (
    "\U0001f305 *Good Morning!*\n\n"
    "A new workday has started. Would you like to create today's labor report?\n\n"
    "Use /new to start fresh, or /copy to copy yesterday's report."
)

LATE_MORNING_REMINDER = (
    "\u23f0 *Reminder*\n\n"
    "Today's labor report hasn't been created yet.\n"
    "The deadline is at *2:00 PM*.\n\n"
    "Use /new to create it now, or /start for the dashboard."
)

AFTERNOON_REMINDER = (
    "\u26a0\ufe0f *Final Reminder*\n\n"
    "The submission deadline is approaching!\n"
    "Today's labor report is still missing.\n\n"
    "Use /new to create it now before the deadline passes."
)

NO_REPORT_CREATED = (
    "\U0001f4cb *End of Workday*\n\n"
    "No labor report was created for today.\n"
    "An empty day record has been saved.\n\n"
    "If this is a mistake, use /new to create a report or contact your admin."
)


class NotificationManager:
    """Scheduled notification service for daily labor report reminders.

    Runs a background asyncio task that periodically checks the current time
    (in the configured timezone) and sends notifications to users at preset
    times. At end of day, auto-creates a ``no_report`` database entry and
    optionally generates an empty Excel file.

    Usage:
        manager = NotificationManager(app, db_manager, config)
        await manager.start()  # Launches background task
        # ... bot runs ...
        await manager.stop()   # Cancels background task
    """

    def __init__(
        self,
        application: Application,
        db_manager: DatabaseManager,
        config: AppConfig,
    ) -> None:
        """Initialize the notification manager.

        Args:
            application: The running Telegram bot Application (for bot access).
            db_manager: Database manager for querying users and creating reports.
            config: Application configuration (timezone + notification settings).
        """
        self._app = application
        self._db = db_manager
        self._config = config
        self._repo = ReportRepository(db_manager)
        # Stage 1 (028): single authoritative calendar policy for the
        # deployment origin site (same times/messages as before).
        self._calendar = WorkingCalendar(config)

        # Tracking state
        self._sent_today: set[str] = set()
        self._last_check_date: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # Resolve timezone
        self._tz_name = config.timezone.name
        self._tz = None
        if self._tz_name and ZoneInfo is not None:
            try:
                self._tz = ZoneInfo(self._tz_name)
            except (ValueError, TypeError):
                logger.warning("Invalid timezone '%s', using system local time", self._tz_name)
                self._tz = None

    # ─── Lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background notification checker task."""
        if self._running:
            logger.warning("Notification manager is already running")
            return

        if not self._config.notification.enabled:
            logger.info("Notification manager is disabled by config")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Notification manager started (timezone=%s, interval=%ds)",
            self._tz_name or "local",
            self._config.notification.check_interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the background notification checker task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Notification manager stopped")

    # ─── Core loop ────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Main background loop — checks time and sends notifications.

        Performs an immediate check on start, then polls every
        ``check_interval_seconds``.
        """
        try:
            # Immediate check on start (captures current hour)
            try:
                await self._check_and_notify()
            except Exception as e:
                logger.error("Notification check failed: %s", e, exc_info=True)

            while self._running:
                await asyncio.sleep(self._config.notification.check_interval_seconds)
                try:
                    await self._check_and_notify()
                except Exception as e:
                    logger.error("Notification check failed: %s", e, exc_info=True)
        except asyncio.CancelledError:
            logger.debug("Notification manager loop cancelled")
            raise

    async def _check_and_notify(self) -> None:
        """Check the current time and trigger notifications if applicable.

        On holidays and Fridays, automatically creates a no_report entry
        (if one doesn't exist) and skips all reminders.
        """
        now = self._now()
        today_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        current_minute = now.minute

        # Reset daily tracking when date changes
        if self._last_check_date != today_str:
            logger.debug("New day detected: %s, resetting notification tracking", today_str)
            self._sent_today.clear()
            self._last_check_date = today_str

        cfg = self._config.notification

        # Check if today is a required workday (028: single policy)
        required, reason = self._calendar.describe(today_str)

        # On non-working days: auto-create no_report at 9 AM (start of day)
        # and skip all notification reminders.
        if not required:
            holiday_name = reason or "Holiday"
            if self._is_time_match(current_hour, current_minute,
                                    cfg.morning_reminder_hour, cfg.morning_reminder_minute):
                has_report = self._has_today_report(today_str)
                if not has_report:
                    await self._auto_create_no_report(today_str, now)
                    logger.info("Auto-created no_report for holiday: %s (%s)", today_str, holiday_name)
            # Skip all other notifications on holidays
            return

        # Check if report exists for today
        has_report = self._has_today_report(today_str)

        # Check each notification time window (±2 minute window)
        # 9 AM — Morning reminder (always sends on working days)
        if self._is_time_match(current_hour, current_minute,
                                cfg.morning_reminder_hour, cfg.morning_reminder_minute):
            await self._send_notification("morning", today_str)

        # 11 AM — Late morning reminder (only if no report)
        if not has_report and self._is_time_match(current_hour, current_minute,
                                                   cfg.late_morning_reminder_hour, cfg.late_morning_reminder_minute):
            await self._send_notification("late_morning", today_str)

        # 2 PM — Afternoon reminder (only if no report)
        if not has_report and self._is_time_match(current_hour, current_minute,
                                                   cfg.afternoon_reminder_hour, cfg.afternoon_reminder_minute):
            await self._send_notification("afternoon", today_str)

        # At/after deadline — Auto-finalize today's drafts (even if report exists)
        # Uses >= check instead of window-match so it fires even if bot starts late.
        auto_hour = self._config.lifecycle.auto_finalize_hour
        auto_min = self._config.lifecycle.auto_finalize_minute
        if auto_hour >= 0:  # -1 disables auto-finalize
            current_total = current_hour * 60 + current_minute
            target_total = auto_hour * 60 + auto_min
            if current_total >= target_total:
                await self._auto_finalize_today_drafts()

        # 5 PM — Auto-create no_report (only if no report exists)
        if not has_report and self._is_time_match(current_hour, current_minute,
                                                   cfg.auto_no_report_hour, cfg.auto_no_report_minute):
            await self._auto_create_no_report(today_str, now)
            await self._send_notification("no_report_created", today_str)

    # ─── Time helpers ─────────────────────────────────────────────

    def _now(self) -> datetime:
        """Get current time in configured timezone."""
        if self._tz is not None:
            return datetime.now(self._tz)
        return datetime.now()

    @staticmethod
    def _is_time_match(
        current_hour: int, current_minute: int,
        target_hour: int, target_minute: int,
        window_minutes: int = 2,
    ) -> bool:
        """Check if current time is within the notification window.

        Args:
            current_hour: Current hour.
            current_minute: Current minute.
            target_hour: Target notification hour.
            target_minute: Target notification minute.
            window_minutes: How many minutes after the target to consider a match.

        Returns:
            True if current time is within the window.
        """
        target_total = target_hour * 60 + target_minute
        current_total = current_hour * 60 + current_minute
        return target_total <= current_total < target_total + window_minutes

    # ─── Database helpers ─────────────────────────────────────────

    def _has_today_report(self, today_str: str) -> bool:
        """Check if a report exists for today (excluding no_report)."""
        try:
            report = self._repo.get_by_date(today_str)
            if report is None:
                return False
            return report.status != ReportStatus.NO_REPORT
        except Exception:
            return False

    def _get_user_ids(self) -> set[str]:
        """Reminder recipients for THIS deployment's origin site (Phase 1).

        Membership + capability are the authority: only active members of
        the origin site who can create daily reports (or hold
        view_site_reports in admin-only mode) get the reminder. The old
        all-approved broadcast leaked site B staff into site A's pings.
        """
        user_ids: set[str] = set()

        try:
            auth_service = self._app.bot_data.get("authorization_service")

            if auth_service is None:
                # Fallback: query distinct telegram_user values
                # (no tenancy authority available here; single-site only).
                query_tables = [
                    "SELECT DISTINCT telegram_user FROM reports WHERE telegram_user IS NOT NULL",
                    "SELECT DISTINCT telegram_user FROM event_log WHERE telegram_user IS NOT NULL",
                    "SELECT DISTINCT telegram_user FROM recent_contractors WHERE telegram_user IS NOT NULL",
                ]
                for sql in query_tables:
                    rows = self._db.execute(sql).fetchall()
                    for row in rows:
                        uid = str(row["telegram_user"]).strip()
                        if uid:
                            user_ids.add(uid)
                return user_ids

            from app.database import driver

            origin = driver.site_id()
            if self._config.notification.send_to_admin_only:
                return set(auth_service.chat_ids_for_site(
                    origin, "view_site_reports"))

            return set(auth_service.chat_ids_for_site(
                origin, "create_daily_report"))

        except Exception as e:
            logger.error("Failed to get user IDs: %s", e)
            return user_ids

    # ─── Notification sending ─────────────────────────────────────

    async def _send_notification(self, notification_type: str, today_str: str) -> None:
        """Send a notification and mark it as sent for today.

        Args:
            notification_type: Key for dedup tracking ('morning', 'late_morning', etc.).
            today_str: Today's date string.
        """
        tracking_key = f"{today_str}:{notification_type}"

        if tracking_key in self._sent_today:
            return

        message = self._get_message(notification_type)
        user_ids = self._get_user_ids()

        if not user_ids:
            logger.info("No users to notify for %s", notification_type)
            self._sent_today.add(tracking_key)
            return

        bot = self._app.bot
        sent_count = 0

        for uid in user_ids:
            try:
                await bot.send_message(
                    chat_id=int(uid),
                    text=message,
                    parse_mode="Markdown",
                )
                sent_count += 1
            except Exception as e:
                logger.warning("Failed to send %s notification to user %s: %s", notification_type, uid, e)

        logger.info("Sent %s notification to %d user(s) (type=%s)", sent_count, len(user_ids), notification_type)
        self._sent_today.add(tracking_key)

    def _get_message(self, notification_type: str) -> str:
        """Get the message text for a notification type."""
        messages = {
            "morning": MORNING_REMINDER,
            "late_morning": LATE_MORNING_REMINDER,
            "afternoon": AFTERNOON_REMINDER,
            "no_report_created": NO_REPORT_CREATED,
        }
        return messages.get(notification_type, "")

    # ─── Admin notification ───────────────────────────────────────

    async def send_admin_notification(self, admin_chat_id: str, message: str, timeout: float = 10.0) -> bool:
        """Send an immediate notification to a specific admin.

        Args:
            admin_chat_id: The admin's Telegram chat ID.
            message: The message text to send.
            timeout: Maximum seconds to wait (default 10).

        Returns:
            True if sent successfully.
        """
        import asyncio
        bot = self._app.bot
        try:
            await asyncio.wait_for(
                bot.send_message(chat_id=int(admin_chat_id), text=message, parse_mode="Markdown"),
                timeout=timeout,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning("Admin notification to %s timed out after %ss", admin_chat_id, timeout)
            return False
        except Exception as e:
            logger.error("Failed to send admin notification to %s: %s", admin_chat_id, e)
            return False

    async def send_broadcast(
        self,
        message: str,
        chat_ids: set[str],
        parse_mode: str = "Markdown",
        per_user_timeout: float = 5.0,
    ) -> tuple[int, int]:
        """Send a broadcast message to a set of users.

        Args:
            message: The message text to send.
            chat_ids: Set of Telegram chat ID strings.
            parse_mode: Parse mode for the message (default Markdown).
            per_user_timeout: Maximum seconds per user (default 5).

        Returns:
            Tuple of (sent_count, total_count).
        """
        import asyncio
        if not chat_ids:
            logger.info("No chat IDs provided for broadcast")
            return (0, 0)

        bot = self._app.bot
        sent = 0
        total = len(chat_ids)

        for cid in chat_ids:
            try:
                await asyncio.wait_for(
                    bot.send_message(chat_id=int(cid), text=message, parse_mode=parse_mode),
                    timeout=per_user_timeout,
                )
                sent += 1
            except asyncio.TimeoutError:
                logger.warning("Broadcast to %s timed out after %ss", cid, per_user_timeout)
            except Exception as e:
                logger.warning("Failed to send broadcast to %s: %s", cid, e)

        logger.info("Broadcast sent to %d/%d users (timeout=%ss per user)", sent, total, per_user_timeout)
        return (sent, total)

    # ─── Auto-finalize ───────────────────────────────────────────

    async def _auto_finalize_today_drafts(self) -> None:
        """Auto-finalize today's draft reports at the configured deadline.

        Retrieves the ``ReportWorkflowService`` from ``bot_data`` and runs
        the explicit per-site loop: each configured site is evaluated
        against ITS OWN WorkingCalendar, so a day off at one site never
        suppresses (or triggers) finalization at another (3.1).
        """
        tracking_key = f"{self._last_check_date or 'unknown'}:auto_finalize"
        if tracking_key in self._sent_today:
            return

        try:
            workflow = self._app.bot_data.get("workflow_service")
            if workflow is None:
                logger.warning("Workflow service not available for auto-finalize")
                return

            from app.services.working_calendar import WorkingCalendar

            raw_sites = getattr(self._config, "sites", None) or []
            site_ids = [str(s.get("id")) for s in raw_sites
                        if isinstance(s, dict) and s.get("id")]
            if not site_ids:
                from app.database import driver

                site_ids = [driver.site_id()]
            counts = workflow.auto_finalize_sites(
                site_ids,
                lambda s: WorkingCalendar(self._config, s),
                telegram_user="system")
            total = sum(counts.values())
            if total > 0:
                logger.info("Auto-finalized draft report(s) at deadline: %s",
                            counts)
                self._sent_today.add(tracking_key)
        except Exception as e:
            logger.error("Auto-finalize failed: %s", e, exc_info=True)

    # ─── Auto-create no_report ────────────────────────────────────

    async def _auto_create_no_report(self, today_str: str, now: datetime) -> None:
        """Create a no_report entry for today and optionally generate empty Excel.

        Args:
            today_str: Today's date string (YYYY-MM-DD).
            now: Current datetime (for day name formatting).
        """
        try:
            # Check again if report exists (race condition guard)
            if self._has_today_report(today_str):
                logger.info("Report already exists for %s, skipping auto-create", today_str)
                return

            # Parse the date for Arabic formatting
            try:
                target_date = date.fromisoformat(today_str)
            except (ValueError, TypeError):
                target_date = now.date()

            day_name = ArabicDateService.get_day_name(target_date)

            report = Report(
                date=today_str,
                day=day_name,
                status=ReportStatus.NO_REPORT,
                telegram_user="system",
            )

            self._repo.add(report)
            logger.info("Auto-created no_report entry for %s", today_str)

            # Generate empty document using empty-day.ots template if available
            await self._generate_empty_doc(today_str)

        except DatabaseError:
            logger.info("Report already exists for %s (concurrent creation)", today_str)
        except Exception as e:
            logger.error("Failed to auto-create no_report for %s: %s", today_str, e)

    async def _generate_empty_doc(self, today_str: str) -> None:
        """Generate an empty document file by copying the empty-day.ots template.

        Args:
            today_str: Today's date string for the output filename.
        """
        empty_template = Path(self._config.notification.empty_template_file)
        if not empty_template.exists():
            logger.info("Empty template not found at %s, skipping document generation", empty_template)
            return

        try:
            import shutil

            output_dir = self._config.docs_folder_path
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"empty_{today_str}.ods"

            shutil.copy2(str(empty_template), str(output_path))
            logger.info("Copied empty template to %s", output_path)
        except Exception as e:
            logger.error("Failed to generate empty document for %s: %s", today_str, e)


# ─── Convenience function ──────────────────────────────────────────


async def setup_notification_manager(
    application: Application,
    db_manager: DatabaseManager,
    config: AppConfig,
) -> NotificationManager:
    """Create, configure, and start the notification manager.

    Args:
        application: The running Telegram bot Application.
        db_manager: Database manager instance.
        config: Application configuration.

    Returns:
        The started NotificationManager instance.
    """
    manager = NotificationManager(application, db_manager, config)
    manager.start()
    return manager
