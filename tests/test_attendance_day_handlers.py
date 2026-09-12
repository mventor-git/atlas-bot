"""Attendance day handler flows (ticket-026): mocked Telegram, real services."""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import attendance as att_handlers
from app.database.manager import DatabaseManager
from app.models.attendance import AttendanceStatus, DayStatus
from app.repositories.attendance_day_repository import AttendanceDayRepository
from app.repositories.attendance_repository import AttendanceRepository
from app.services.attendance_day_service import AttendanceDayService
from app.services.attendance_service import AttendanceService

SITE = "default"


def make_update(user_id=111, text=""):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = f"User{user_id}"
    update.effective_user.last_name = ""
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.reply_text = AsyncMock()
    msg.photo = []
    msg.location = None
    update.message = msg
    update.callback_query = None
    update.effective_message = msg
    return update


def make_callback(user_id, data):
    update = make_update(user_id)
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def make_auth(role="normal_user"):
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session=None: session or SITE)
    auth.sites_for_user.return_value = [SITE]
    auth.chat_ids_for_site.return_value = ["111", "222"]
    return auth


def make_context(auth, att, day, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {
        "authorization_service": auth,
        "attendance_service": att,
        "attendance_day_service": day,
        "app_config": MagicMock(sites=[]),
    }
    context.user_data = user_data if user_data is not None else {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def services():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "a.db"))
        events = AttendanceRepository(manager)
        yield (AttendanceService(events),
               AttendanceDayService(AttendanceDayRepository(manager), events))
        manager.close_all()


@pytest.fixture
def flow_data():
    return {}


def _confirmed_day(day, chat="111"):
    d = day.ensure_day(chat, date.today().isoformat(), site_id=SITE)
    d.status = DayStatus.CONFIRMED
    return day._days.update_day(d)


class TestMyDay:
    async def test_pending_day_lists_anomalies_not_absence(
            self, services, flow_data):
        att, day = services
        ctx = make_context(make_auth(), att, day, flow_data)
        upd = make_update(111, "/myday")
        await att_handlers.myday_command(upd, ctx)
        out = upd.effective_message.reply_text.call_args[0][0]
        assert "pending" in out and "missing_evidence" in out
        assert "absent" not in out

    async def test_pending_denied(self, services, flow_data):
        att, day = services
        ctx = make_context(make_auth("pending"), att, day, flow_data)
        upd = make_update(111, "/myday")
        await att_handlers.myday_command(upd, ctx)
        assert "approved account" in \
            upd.effective_message.reply_text.call_args[0][0]


class TestQueue:
    async def test_sweep_lists_missing_evidence(self, services, flow_data):
        att, day = services
        ctx = make_context(make_auth("project_manager"), att, day, flow_data)
        upd = make_update(222, "/attendance_queue")
        await att_handlers.queue_command(upd, ctx)
        out = upd.effective_message.reply_text.call_args[0][0]
        assert "pending" in out and "absent" not in out
        assert day._days.get_day("222", date.today().isoformat(),
                                 site_id=SITE) is not None

    async def test_queue_denied_for_normal(self, services, flow_data):
        att, day = services
        ctx = make_context(make_auth("normal_user"), att, day, flow_data)
        upd = make_update(111, "/attendance_queue")
        await att_handlers.queue_command(upd, ctx)
        assert "attendance manager" in \
            upd.effective_message.reply_text.call_args[0][0].lower()


class TestDayResolve:
    async def test_resolve_flow(self, services, flow_data):
        att, day = services
        _confirmed_day(day)
        ctx = make_context(make_auth("project_manager"), att, day, flow_data)
        d = day._days.get_day("111", date.today().isoformat(), site_id=SITE)
        await att_handlers.handle_day_callback(
            make_callback(222, f"day_resolve:{d.id}"), ctx)
        assert flow_data["state"] == "awaiting_day_resolve"
        await att_handlers.handle_day_resolve(
            make_update(222, "present | on time, verified"), ctx)
        got = day._days.get_day("111", date.today().isoformat(), site_id=SITE)
        assert got.status == DayStatus.RESOLVED and got.verdict == "present"
        ctx.bot.send_message.assert_called_once()  # employee notified

    async def test_resolve_denied_for_normal(self, services, flow_data):
        att, day = services
        _confirmed_day(day)
        ctx = make_context(make_auth("normal_user"), att, day, flow_data)
        d = day._days.get_day("111", date.today().isoformat(), site_id=SITE)
        upd = make_callback(111, f"day_resolve:{d.id}")
        await att_handlers.handle_day_callback(upd, ctx)
        assert "attendance manager" in \
            upd.callback_query.edit_message_text.call_args[0][0].lower()


class TestDayDispute:
    async def test_subject_dispute_flow(self, services, flow_data):
        att, day = services
        _confirmed_day(day)
        ctx = make_context(make_auth(), att, day, flow_data)
        d = day._days.get_day("111", date.today().isoformat(), site_id=SITE)
        await att_handlers.handle_day_callback(
            make_callback(111, f"day_dispute:{d.id}"), ctx)
        assert flow_data["state"] == "awaiting_day_note"
        await att_handlers.handle_day_dispute_note(
            make_update(111, "bus broke down"), ctx)
        got = day._days.get_day("111", date.today().isoformat(), site_id=SITE)
        assert got.status == DayStatus.DISPUTED

    async def test_stranger_dispute_refused(self, services, flow_data):
        att, day = services
        _confirmed_day(day)
        ctx = make_context(make_auth(), att, day, flow_data)
        d = day._days.get_day("111", date.today().isoformat(), site_id=SITE)
        upd = make_callback(999, f"day_dispute:{d.id}")
        await att_handlers.handle_day_callback(upd, ctx)
        assert "Only the employee" in \
            upd.callback_query.edit_message_text.call_args[0][0]


class TestNotesClaims:
    async def test_daynote_by_manager(self, services, flow_data):
        att, day = services
        ctx = make_context(make_auth("project_manager"), att, day, flow_data)
        await att_handlers.daynote_command(
            make_update(222, "/daynote 111 2026-09-11 seen on site"), ctx)
        claims = day._days.claims_for_day("111", "2026-09-11", site_id=SITE)
        assert len(claims) == 1 and claims[0].kind == "note"

    async def test_daynote_denied_for_normal(self, services, flow_data):
        att, day = services
        ctx = make_context(make_auth("normal_user"), att, day, flow_data)
        upd = make_update(111, "/daynote 222 2026-09-11 hi")
        await att_handlers.daynote_command(upd, ctx)
        assert "attendance manager" in \
            upd.effective_message.reply_text.call_args[0][0].lower()

    async def test_claim_and_decide(self, services, flow_data):
        att, day = services
        ctx = make_context(make_auth(), att, day, flow_data)
        await att_handlers.dayclaim_command(
            make_update(111, f"/day_claim {date.today().isoformat()} device dead"),
            ctx)
        claims = day._days.claims_for_day(
            "111", date.today().isoformat(), site_id=SITE)
        assert len(claims) == 1
        mgr = make_context(make_auth("project_manager"), att, day, flow_data)
        await att_handlers.handle_day_callback(
            make_callback(222, f"day_claim_ok:{claims[0].id}"), mgr)
        assert flow_data["state"] == "awaiting_claim_note"
        await att_handlers.handle_claim_note(
            make_update(222, "verified with PM"), mgr)
        got = day._days.get_claim(claims[0].id, site_id=SITE)
        assert got.status == "approved"
