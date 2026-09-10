"""HR handler flow tests (ticket-006-C): mocked Telegram, real chain engine."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import hr as hr_handlers
from app.database.manager import DatabaseManager
from app.models.hr import HRRequestStatus
from app.repositories.hr_repository import HRRepository
from app.services.hr_service import HRService


def make_update(user_id=111, text="", callback_data=None):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = f"User{user_id}"
    update.effective_user.last_name = ""
    if callback_data is not None:
        cq = MagicMock(spec=CallbackQuery)
        cq.data = callback_data
        cq.answer = AsyncMock()
        cq.edit_message_text = AsyncMock()
        update.callback_query = cq
        update.message = None
        update.effective_message = MagicMock()
        update.effective_message.reply_text = AsyncMock()
    else:
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
        lambda chat_id, capability, site_id=None: capability in caps.for_role(role)
    )
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
        manager = DatabaseManager(str(Path(tmp) / "hr.db"))
        yield HRService(HRRepository(manager))
        manager.close_all()


@pytest.fixture
def user_data():
    return {}


class TestAdvanceFlow:
    async def test_full_advance_flow(self, service, user_data):
        auth = make_auth("normal_user")
        ctx = make_context(auth, service, user_data)
        await hr_handlers.advance_command(make_update(111, "/advance"), ctx)
        assert user_data["state"] == "awaiting_hr_amount"
        await hr_handlers.handle_hr_amount(make_update(111, "1500"), ctx)
        assert user_data["state"] == "awaiting_hr_reason"
        await hr_handlers.handle_hr_reason(make_update(111, "medical"), ctx)
        assert user_data.get("state") is None
        rows = service.visible_to("111", "normal_user")
        assert len(rows) == 1
        assert rows[0].status == HRRequestStatus.PENDING
        assert rows[0].amount == 1500

    async def test_bad_amount_reprompts(self, service, user_data):
        ctx = make_context(make_auth(), service, user_data)
        await hr_handlers.advance_command(make_update(111, "/advance"), ctx)
        await hr_handlers.handle_hr_amount(make_update(111, "zero"), ctx)
        assert user_data["state"] == "awaiting_hr_amount"

    async def test_pending_user_blocked(self, service, user_data):
        ctx = make_context(make_auth("pending"), service, user_data)
        await hr_handlers.advance_command(make_update(111, "/advance"), ctx)
        assert "state" not in user_data or user_data.get("state") != "awaiting_hr_amount"


class TestTransportFlow:
    async def test_transport_with_skip(self, service, user_data):
        ctx = make_context(make_auth(), service, user_data)
        await hr_handlers.transport_command(make_update(111, "/transport"), ctx)
        await hr_handlers.handle_hr_amount(make_update(111, "200"), ctx)
        await hr_handlers.handle_hr_reason(make_update(111, "site visit"), ctx)
        assert user_data["state"] == "awaiting_hr_trip_date"
        await hr_handlers.handle_hr_trip_date(make_update(111, "2026-09-01"), ctx)
        assert user_data["state"] == "awaiting_hr_report_ref"
        await hr_handlers.handle_hr_skip(make_update(111, "/hr_skip"), ctx)
        assert user_data["state"] == "awaiting_hr_receipt"
        await hr_handlers.handle_hr_skip(make_update(111, "/hr_skip"), ctx)
        rows = service.visible_to("111", "normal_user")
        assert len(rows) == 1
        assert rows[0].trip_date == "2026-09-01"


class TestApprovalCallbacks:
    async def _filed_advance(self, service, user_data):
        ctx = make_context(make_auth(), service, user_data)
        await hr_handlers.advance_command(make_update(111, "/advance"), ctx)
        await hr_handlers.handle_hr_amount(make_update(111, "1500"), ctx)
        await hr_handlers.handle_hr_reason(make_update(111, "medical"), ctx)
        return service.visible_to("111", "normal_user")[0]

    async def test_pm_confirm_notifies(self, service, user_data):
        req = await self._filed_advance(service, user_data)
        ctx = make_context(make_auth("project_manager"), service, {})
        upd = make_update(222, callback_data=f"hr_confirm:{req.id}")
        await hr_handlers.handle_hr_callback(upd, ctx)
        assert service._repo.get_by_id(req.id).status == HRRequestStatus.PM_CONFIRMED
        ctx.bot.send_message.assert_called_once()

    async def test_non_pm_cannot_confirm(self, service, user_data):
        req = await self._filed_advance(service, user_data)
        ctx = make_context(make_auth("normal_user"), service, {})
        upd = make_update(333, callback_data=f"hr_confirm:{req.id}")
        await hr_handlers.handle_hr_callback(upd, ctx)
        assert service._repo.get_by_id(req.id).status == HRRequestStatus.PENDING

    async def test_approve_advance_month_flow(self, service, user_data):
        req = await self._filed_advance(service, user_data)
        pm = make_context(make_auth("project_manager"), service, {})
        await hr_handlers.handle_hr_callback(
            make_update(222, callback_data=f"hr_confirm:{req.id}"), pm)
        hr = make_context(make_auth("hr"), service, {})
        upd = make_update(333, callback_data=f"hr_approve:{req.id}")
        await hr_handlers.handle_hr_callback(upd, hr)
        month_upd = make_update(333, callback_data=f"hr_month:2026-10:{req.id}")
        await hr_handlers.handle_hr_callback(month_upd, hr)
        decided = service._repo.get_by_id(req.id)
        assert decided.status == HRRequestStatus.APPROVED
        assert decided.deduction_month == "2026-10"

    async def test_reject_note_flow(self, service, user_data):
        req = await self._filed_advance(service, user_data)
        pm = make_context(make_auth("project_manager"), service, {})
        await hr_handlers.handle_hr_callback(
            make_update(222, callback_data=f"hr_confirm:{req.id}"), pm)
        hr_data = {}
        hr = make_context(make_auth("hr"), service, hr_data)
        await hr_handlers.handle_hr_callback(
            make_update(333, callback_data=f"hr_reject:{req.id}"), hr)
        assert hr_data["state"] == "awaiting_reject_note"
        await hr_handlers.handle_reject_note(make_update(333, "no budget"), hr)
        decided = service._repo.get_by_id(req.id)
        assert decided.status == HRRequestStatus.REJECTED
        assert decided.note == "no budget"

    async def test_delegate_flow(self, service, user_data):
        req = await self._filed_advance(service, user_data)
        pm_data = {}
        pm = make_context(make_auth("project_manager"), service, pm_data)
        auth = pm.bot_data["authorization_service"]
        auth.get_role.side_effect = lambda cid: "hr" if cid == "999" else "project_manager"
        await hr_handlers.handle_hr_callback(
            make_update(222, callback_data=f"hr_delegate:{req.id}"), pm)
        assert pm_data["state"] == "awaiting_delegate_target"
        await hr_handlers.handle_delegate_target(make_update(222, "999"), pm)
        delegated = service._repo.get_by_id(req.id)
        assert delegated.delegated is True
        assert delegated.assigned_to == "999"

    async def test_non_hr_approve_refused(self, service, user_data):
        req = await self._filed_advance(service, user_data)
        pm = make_context(make_auth("project_manager"), service, {})
        await hr_handlers.handle_hr_callback(
            make_update(222, callback_data=f"hr_confirm:{req.id}"), pm)
        ctx = make_context(make_auth("normal_user"), service, {})
        upd = make_update(111, callback_data=f"hr_approve:{req.id}")
        await hr_handlers.handle_hr_callback(upd, ctx)
        assert service._repo.get_by_id(req.id).status == HRRequestStatus.PM_CONFIRMED


class TestQueues:
    async def test_pending_and_my(self, service, user_data):
        ctx = make_context(make_auth(), service, user_data)
        await hr_handlers.advance_command(make_update(111, "/advance"), ctx)
        await hr_handlers.handle_hr_amount(make_update(111, "100"), ctx)
        await hr_handlers.handle_hr_reason(make_update(111, "x"), ctx)
        pm = make_context(make_auth("project_manager"), service, {})
        upd = make_update(222, callback_data="hr_pending")
        await hr_handlers.handle_hr_callback(upd, pm)
        upd.effective_message.reply_text.assert_called()
        my = make_update(111, callback_data="hr_my")
        await hr_handlers.handle_hr_callback(my, ctx)
        my.effective_message.reply_text.assert_called()
