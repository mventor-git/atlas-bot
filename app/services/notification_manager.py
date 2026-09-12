"""
Notification Manager — durable scheduled reminders for Atlas (031 rewrite).

Every scheduled send goes through the persistent outbox: enqueue with a
deterministic dedup key, then dispatch with eligibility re-checks, failure
classification, and backoff. The in-memory _sent_today set is gone; restarts
can neither duplicate nor lose notifications.

Per-tick flow: for each configured site (isolated try/except), evaluate the
site's WorkingCalendar, run that site's windows, then drain due outbox rows.
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
from app.repositories.notification_repository import NotificationRepository
from app.repositories.report_repository import ReportRepository
from app.services.arabic_date_service import ArabicDateService
from app.services.notification_outbox import NotificationOutbox
from app.services.working_calendar import WorkingCalendar
from app.utils.exceptions import DatabaseError

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)


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
        outbox: NotificationOutbox | None = None,
    ) -> None:
        """Initialize the notification manager.

        Args:
            application: The running Telegram bot Application (for bot access).
            db_manager: Database manager for querying users and creating reports.
            config: Application configuration (timezone + notification settings).
            outbox: Durable outbox (built from db_manager when omitted).
        """
        self._app = application
        self._db = db_manager
        self._config = config
        self._repo = ReportRepository(db_manager)
        self.outbox = outbox or NotificationOutbox(
            NotificationRepository(db_manager),
            max_attempts=config.notification.notify_max_attempts,
            backoff_min=tuple(config.notification.notify_retry_backoff_min))

        # Tracking state (loop only; all send history is durable)
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

    async def _check_and_notify(self, today_str=None, hour=None, minute=None) -> dict:
        """One scheduler pass: per-site evaluation + outbox drain.

        Each site runs isolated (a site failure never stops the others);
        each site uses its own WorkingCalendar. Returns {site: summary}.
        Optional overrides make the pass deterministic for tests/cycles.
        """
        now = self._now()
        today_str = today_str or now.strftime("%Y-%m-%d")
        hour = now.hour if hour is None else hour
        minute = now.minute if minute is None else minute

        out: dict = {}
        for site in self._site_ids():
            try:
                out[site] = await self._check_site(site, today_str, now,
                                                   hour, minute)
            except Exception as e:
                logger.error("Site sweep failed for %s: %s", site, e,
                             exc_info=True)
                out[site] = {"error": str(e)[:120]}
        await self._drain()
        return out

    async def run_cycle(self, today_str=None, hour=None,
                        minute=None) -> dict:
        """Deterministic single cycle (§28 trial utility + tests)."""
        return await self._check_and_notify(today_str, hour, minute)

    def pending_count(self, site_id=None) -> int:
        return self._outbox_repo().pending_count(site_id)

    def _outbox_repo(self):
        from app.repositories.notification_repository import (
            NotificationRepository)

        return NotificationRepository(self._db)

    def _outbox(self):
        from app.services.notification_outbox import NotificationOutbox

        cfg = self._config.notification
        return NotificationOutbox(
            self._outbox_repo(), max_attempts=cfg.notify_max_attempts,
            backoff_min=tuple(cfg.notify_retry_backoff_min))

    def _site_ids(self) -> list:
        raw = getattr(self._config, "sites", None) or []
        ids = [str(s.get("id")) for s in raw
               if isinstance(s, dict) and s.get("id")]
        if ids:
            return ids
        from app.database import driver

        return [driver.site_id()]

    async def _check_site(self, site: str, today_str: str, now: datetime,
                          hour: int, minute: int) -> dict:
        """Evaluate one site's windows; enqueue (never send directly)."""
        from app.services.working_calendar import WorkingCalendar

        cfg = self._config.notification
        calendar = WorkingCalendar(self._config, site)
        required, reason = calendar.describe(today_str)
        done: dict = {"site": site, "required": required}

        if not required:
            if self._is_time_match(hour, minute,
                                   cfg.morning_reminder_hour,
                                   cfg.morning_reminder_minute):
                if not self._has_today_report(today_str, site):
                    await self._auto_create_no_report(today_str, now, site)
            done["skipped"] = reason
            return done

        has_report = self._has_today_report(today_str, site)
        creators = self._roster(site, "create_daily_report")

        def _enq(ntype, to, ref="", priority=0, level=0, **kw):
            return self.outbox.enqueue(
                ntype, to, site, reference=ref or f"missing:{today_str}",
                priority=priority, level=level, date=today_str, **kw)

        # 9 AM — Morning reminder (always on working days)
        if self._is_time_match(hour, minute,
                               cfg.morning_reminder_hour,
                               cfg.morning_reminder_minute):
            for uid in creators:
                _enq("morning", uid)
            done["morning"] = len(creators)

        # 11 AM / 2 PM — follow-ups only when missing
        if not has_report:
            if self._is_time_match(hour, minute,
                                   cfg.late_morning_reminder_hour,
                                   cfg.late_morning_reminder_minute):
                for uid in creators:
                    _enq("late_morning", uid)
                done["late_morning"] = len(creators)
            if self._is_time_match(hour, minute,
                                   cfg.afternoon_reminder_hour,
                                   cfg.afternoon_reminder_minute):
                for uid in creators:
                    _enq("afternoon", uid)
                done["afternoon"] = len(creators)
                await self._sweep_attendance(site, today_str)
                await self._sweep_overtime(site, today_str)

        # Escalation: past deadline + window, still missing -> reviewers
        auto_hour = self._config.lifecycle.auto_finalize_hour
        auto_min = self._config.lifecycle.auto_finalize_minute
        if auto_hour >= 0 and not has_report:
            current_total = hour * 60 + minute
            esc_total = (auto_hour * 60 + auto_min
                         + cfg.escalation_after_min)
            if current_total >= esc_total:
                reviewers = self._roster(site, "approve_daily_report")
                for uid in reviewers:
                    _enq("report_missing", uid, priority=1, level=1)
                done["escalated"] = len(reviewers)

        # At/after deadline — finalize drafts (>= check: fires even if late)
        if auto_hour >= 0:
            current_total = hour * 60 + minute
            target_total = auto_hour * 60 + auto_min
            if current_total >= target_total:
                await self._auto_finalize_today_drafts([site])

        # 5 PM — auto-create no_report (only if missing) + tell creators
        if not has_report and self._is_time_match(
                hour, minute, cfg.auto_no_report_hour,
                cfg.auto_no_report_minute):
            await self._auto_create_no_report(today_str, now, site)
            for uid in creators:
                _enq("no_report_created", uid)
            done["no_report"] = len(creators)

        # Workflow-driven: FINAL awaiting review -> reviewers (deduped)
        if has_report:
            rep = self._repo.get_by_date(today_str, site_id=site)
            if rep is not None and rep.status == ReportStatus.FINAL:
                for uid in self._roster(site, "approve_daily_report"):
                    _enq("report_review", uid, ref=f"review:{today_str}",
                         priority=1)
                done["review_queued"] = True
        return done

    async def _drain(self) -> dict:
        """Deliver all due outbox rows with eligibility re-checks."""
        bot = self._app.bot
        auth = (self._app.bot_data or {}).get("authorization_service")

        async def _send(to: str, text: str) -> None:
            await bot.send_message(chat_id=int(to), text=text,
                                   parse_mode="Markdown")

        return await self.outbox.dispatch(_send, auth=auth)

    def _roster(self, site: str, capability: str) -> set[str]:
        """Capability holders at a site; empty (fail-closed) without auth."""
        try:
            auth = (self._app.bot_data or {}).get("authorization_service")
            if auth is None:
                logger.warning("No auth service: no roster for %s", site)
                return set()
            return set(auth.chat_ids_for_site(site, capability))
        except Exception as e:
            logger.error("Roster failed for %s: %s", site, e)
            return set()

    def _site_confirmer(self, site: str):
        """Primary confirmer for attendance escalation (site config)."""
        raw = getattr(self._config, "sites", None) or []
        for entry in raw:
            if isinstance(entry, dict) and str(entry.get("id")) == site:
                for key in ("confirmer", "confirmer_fallback"):
                    if entry.get(key):
                        return str(entry[key])
        return None

    async def _sweep_attendance(self, site: str, today_str: str) -> int:
        """Backstop: unresolved attendance days -> site confirmer (deduped)."""
        try:
            day_service = (self._app.bot_data or {}).get(
                "attendance_day_service")
            if day_service is None:
                return 0
            confirmer = self._site_confirmer(site)
            if confirmer is None:
                return 0
            outbox = self.outbox
            n = 0
            for day in day_service.queue(today_str, site_id=site):
                outbox.enqueue("attendance_pending", confirmer, site,
                               reference=f"{today_str}:{day.chat_id}",
                               subject=day.chat_id, kind="check-in",
                               evidence="pending review", date=today_str)
                n += 1
            if n:
                logger.info("Queued %d attendance confirmations for %s @%s",
                            n, confirmer, site)
            return n
        except Exception as e:
            logger.error("Attendance sweep failed for %s: %s", site, e)
            return 0

    async def _sweep_overtime(self, site: str, today_str: str) -> int:
        """Backstop: pending overtime requests -> PMs (deduped)."""
        try:
            hr_service = (self._app.bot_data or {}).get("hr_service")
            if hr_service is None or not hasattr(hr_service, "pending_overtime"):
                return 0
            outbox = self.outbox
            pms = self._roster(site, "confirm_hr_request")
            n = 0
            for req in hr_service.pending_overtime(site_id=site):
                for pm in pms:
                    outbox.enqueue(
                        "overtime_pending", pm, site,
                        reference=f"overtime:{req.id}",
                        subject=req.requester_chat_id,
                        hours=getattr(req, "hours", "?"),
                        date=getattr(req, "trip_date", None)
                        or (req.created_at or "")[:10], priority=1)
                    n += 1
            return n
        except Exception as e:
            logger.error("Overtime sweep failed for %s: %s", site, e)
            return 0

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

    def _has_today_report(self, today_str: str, site: str | None = None) -> bool:
        """Check if a report exists for today (excluding no_report)."""
        try:
            from app.database import driver

            report = self._repo.get_by_date(
                today_str, site_id=site or driver.site_id())
            if report is None:
                return False
            return report.status != ReportStatus.NO_REPORT
        except Exception:
            return False

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

    async def _auto_finalize_today_drafts(self, site_ids=None) -> dict:
        """Auto-finalize today's draft reports at the configured deadline.

        Explicit per-site loop (3.1 contract): each configured site is
        evaluated against ITS OWN WorkingCalendar. Returns {site: count}.
        Idempotent: FINAL-or-later rows are never refinalized; the
        outbox dedups any notices derived from this.
        """
        try:
            workflow = self._app.bot_data.get("workflow_service")
            if workflow is None:
                logger.warning("Workflow service not available for auto-finalize")
                return {}

            from app.services.working_calendar import WorkingCalendar

            if site_ids is None:
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
            return counts
        except Exception as e:
            logger.error("Auto-finalize failed: %s", e, exc_info=True)
            return {}

    # ─── Auto-create no_report ────────────────────────────────────

    async def _auto_create_no_report(self, today_str: str, now: datetime,
                                       site: Optional[str] = None) -> None:
        """Create a no_report entry for today and optionally generate empty Excel.

        Args:
            today_str: Today's date string (YYYY-MM-DD).
            now: Current datetime (for day name formatting).
            site: Tenant site (defaults to deployment origin).
        """
        from app.database import driver

        site = site or driver.site_id()
        try:
            # Check again if report exists (race condition guard)
            if self._has_today_report(today_str, site):
                logger.info("Report already exists for %s @%s, skipping auto-create",
                            today_str, site)
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
                site_id=site,
            )

            self._repo.add(report)
            logger.info("Auto-created no_report entry for %s @%s", today_str, site)

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
