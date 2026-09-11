"""LMO handler flows (ticket-011): mocked Telegram, real chain engine."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import hr as hr_handlers
from app.database.manager import DatabaseManager
from app.models.hr import HRRequestStatus
from app.repositories.hr_repository import HRRepository
from app.repositories.money_repository import MoneyRepository
from app.services.hr_service import HRService


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
    update.message = msg
    update.callback_query = None
    update.effective_message = msg
    return update


def make_auth(role="normal_user"):
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    return auth


def make_context(auth, service, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth, "hr_service": service}
    context.user_data = user_data if user_data is not None else {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "l.db"))
        yield HRService(HRRepository(manager), MoneyRepository(manager))
        manager.close_all()


@pytest.fixture
def flow_data():
    return {}


class TestLeaveFlow:
    async def test_full_leave_flow(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await hr_handlers.leave_command(make_update(111, "/leave"), ctx)
        assert flow_data["state"] == "awaiting_lmo_reason"
        await hr_handlers.handle_lmo_reason(make_update(111, "family"), ctx)
        assert flow_data["state"] == "awaiting_lmo_start"
        await hr_handlers.handle_lmo_start(make_update(111, "2026-10-01"), ctx)
        assert flow_data["state"] == "awaiting_lmo_end"
        await hr_handlers.handle_lmo_end(make_update(111, "2026-10-03"), ctx)
        assert flow_data.get("state") is None
        rows = service.visible_to("111", "normal_user")
        assert len(rows) == 1
        assert rows[0].status == HRRequestStatus.PENDING
        assert rows[0].start_date == "2026-10-01"

    async def test_bad_dates_reprompt(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await hr_handlers.leave_command(make_update(111, "/leave"), ctx)
        await hr_handlers.handle_lmo_reason(make_update(111, "x"), ctx)
        await hr_handlers.handle_lmo_start(make_update(111, "tomorrow"), ctx)
        assert flow_data["state"] == "awaiting_lmo_start"


class TestOvertimeFlow:
    async def test_overtime_with_skip(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await hr_handlers.overtime_command(make_update(111, "/overtime"), ctx)
        await hr_handlers.handle_lmo_date(make_update(111, "2026-10-02"), ctx)
        await hr_handlers.handle_lmo_hours(make_update(111, "3"), ctx)
        assert flow_data["state"] == "awaiting_lmo_reason"
        await hr_handlers.handle_lmo_skip(make_update(111, "/hr_skip"), ctx)
        rows = service.visible_to("111", "normal_user")
        assert len(rows) == 1
        assert rows[0].hours == 3

    async def test_viewer_can_request_leave(self, service, flow_data):
        ctx = make_context(make_auth("viewer"), service, flow_data)
        await hr_handlers.leave_command(make_update(111, "/leave"), ctx)
        assert flow_data["state"] == "awaiting_lmo_reason"

    async def test_pending_blocked(self, service, flow_data):
        ctx = make_context(make_auth("pending"), service, flow_data)
        await hr_handlers.leave_command(make_update(111, "/leave"), ctx)
        assert flow_data.get("state") != "awaiting_lmo_reason"


class TestMissionFlow:
    async def test_mission_flow(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await hr_handlers.mission_command(make_update(111, "/mission"), ctx)
        await hr_handlers.handle_lmo_reason(make_update(111, "client visit"), ctx)
        await hr_handlers.handle_lmo_start(make_update(111, "2026-10-02"), ctx)
        await hr_handlers.handle_lmo_end(make_update(111, "2026-10-02"), ctx)
        rows = service.visible_to("111", "normal_user")
        assert len(rows) == 1
        assert rows[0].end_date == "2026-10-02"
