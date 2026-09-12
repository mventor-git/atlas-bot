"""Working calendar tests (ticket-028, Stage 1)."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

FRIDAY = "2026-09-11"    # a Friday
SATURDAY = "2026-09-12"  # a Saturday
SUNDAY = "2026-09-13"


class StubHolidays:
    """Deterministic stand-in for HolidayCalendarService data."""

    def __init__(self, holidays=None, enforce=True):
        self._days = dict(holidays or {})
        self._enforce_holidays = enforce

    def is_holiday(self, day):
        return self._enforce_holidays and day in self._days

    def get_holiday_name(self, day):
        return self._days.get(day)


def make_cal(site=None, **kw):
    from app.services.working_calendar import WorkingCalendar

    sites = [{"id": "a", "name": "A"}, {"id": "b", "name": "B",
                                        "weekend": [5, 6],
                                        "working_dates": ["2026-12-25"],
                                        "off_dates": ["2026-09-14"]}]
    cfg = SimpleNamespace(sites=sites)
    stub = StubHolidays(kw.pop("holidays", None), kw.pop("enforce", True))
    return WorkingCalendar(cfg, site or "a", holidays=stub)


class TestPolicy:
    def test_friday_default_off(self):
        cal = make_cal()
        assert cal.is_required_workday(FRIDAY) is False
        assert cal.describe(FRIDAY) == (False, "weekend")

    def test_plain_saturday_on(self):
        assert make_cal().is_required_workday(SATURDAY) is True

    def test_named_holiday_off(self):
        cal = make_cal(holidays={SATURDAY: "Test Day"})
        assert cal.is_required_workday(SATURDAY) is False
        assert cal.describe(SATURDAY) == (False, "Test Day")

    def test_working_dates_win_over_friday(self):
        # site b declares Christmas working even though it is a Friday
        cal_b = make_cal("b")
        assert cal_b.is_required_workday("2026-12-25") is True
        assert cal_b.describe("2026-12-25") == (True, "exceptional working day")

    def test_off_dates_win_over_workday(self):
        cal = make_cal("b")
        assert cal.is_required_workday("2026-09-14") is False  # a Monday
        assert cal.describe("2026-09-14") == (False, "site day off")

    def test_site_weekend_honored(self):
        cal = make_cal("b")  # weekend Sat+Sun
        assert cal.is_required_workday(FRIDAY) is True
        assert cal.is_required_workday(SATURDAY) is False
        assert cal.is_required_workday(SUNDAY) is False

    def test_bad_weekend_falls_back_to_friday(self):
        from app.services.working_calendar import WorkingCalendar

        cfg = SimpleNamespace(sites=[{"id": "a", "weekend": "nonsense"}])
        cal = WorkingCalendar(cfg, "a", holidays=StubHolidays())
        assert cal.weekend_days() == [4]
        assert cal.is_required_workday(FRIDAY) is False

    def test_toggle_off_makes_everything_required(self):
        cal = make_cal(enforce=False)
        assert cal.is_required_workday(FRIDAY) is True
        assert cal.describe(FRIDAY) == (True, "calendar disabled")

    def test_invalid_dates_safe(self):
        cal = make_cal()
        assert cal.is_required_workday("not-a-date") is False
        assert cal.is_required_workday("") is False
        assert cal.describe("xx") == (False, None)

    def test_unknown_site_uses_defaults(self):
        cal = make_cal("ghost")
        assert cal.is_required_workday(FRIDAY) is False
        assert cal.is_required_workday(SATURDAY) is True


class TestDayViewKeys:
    def _svc(self, with_calendar):
        import tempfile as _tf

        tmp = _tf.mkdtemp()
        from app.database.manager import DatabaseManager
        from app.repositories.attendance_day_repository import (
            AttendanceDayRepository)
        from app.repositories.attendance_repository import AttendanceRepository
        from app.services.attendance_day_service import AttendanceDayService

        manager = DatabaseManager(str(Path(tmp) / "c.db"))
        days = AttendanceDayRepository(manager)
        events = AttendanceRepository(manager)
        factory = None
        if with_calendar:
            cfg = SimpleNamespace(sites=[{"id": "site-a"}])
            from app.services.working_calendar import WorkingCalendar
            factory = lambda site: WorkingCalendar(  # noqa: E731
                cfg, site, holidays=StubHolidays())
        svc = AttendanceDayService(days, events, calendar_for=factory)
        svc._manager = manager
        return svc

    def test_keys_present_without_calendar(self):
        svc = self._svc(False)
        view = svc.day_view("u1", SATURDAY, site_id="site-a")
        assert view["required"] is None and view["calendar_note"] is None
        svc._manager.close_all()

    def test_friday_flagged_with_calendar(self):
        svc = self._svc(True)
        view = svc.day_view("u1", FRIDAY, site_id="site-a")
        assert view["required"] is False
        svc._manager.close_all()

    async def test_myday_renders_non_working_line(self):
        from app.bot.handlers import attendance as att_handlers
        from app.auth import capabilities as caps
        from app.services.authorization_service import AuthorizationService

        svc = self._svc(True)
        auth = MagicMock(spec=AuthorizationService)
        auth.get_role.return_value = "normal_user"
        auth.has_capability.side_effect = (
            lambda chat_id, cap, site_id=None: cap in caps.for_role("normal_user"))
        auth.resolve_active_site.side_effect = (
            lambda chat_id, session=None: session or "site-a")
        auth.sites_for_user.return_value = ["site-a"]
        ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        ctx.bot_data = {"authorization_service": auth,
                        "attendance_service": MagicMock(),
                        "attendance_day_service": svc,
                        "app_config": MagicMock(sites=[])}
        ctx.user_data = {}
        update = MagicMock(spec=Update)
        update.effective_user = MagicMock()
        update.effective_user.id = 111
        msg = MagicMock(spec=Message)
        msg.text = f"/myday {FRIDAY}"
        msg.reply_text = AsyncMock()
        update.message = msg
        update.effective_message = msg
        await att_handlers.myday_command(update, ctx)
        out = msg.reply_text.call_args[0][0]
        assert "Non-working day" in out
        svc._manager.close_all()
