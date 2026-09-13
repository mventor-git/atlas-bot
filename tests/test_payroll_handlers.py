"""Payroll handler flows (ticket-021): mocked Telegram, real engine."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import payroll as payroll_handlers
from app.database.manager import DatabaseManager
from app.models.database import User
from app.models.payroll import PayrollRunStatus
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.services.payroll_service import PayrollService


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


def make_auth(role="hr"):
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.resolve_active_site.side_effect = (lambda chat_id, session_site=None: session_site or 'default')
    auth.sites_for_user.return_value = ['default']
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    return auth


def make_context(auth, service, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth,
                        "payroll_service": service}
    context.user_data = user_data if user_data is not None else {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "p.db"))
        users = UserRepository(manager)
        users.upsert(User(chat_id="111", role="normal_user"))
        users.upsert(User(chat_id="222", role="hr"))
        users.set_salary("111", 12000.0, effective_from="2026-01-01")
        users.set_salary("222", 15000.0, effective_from="2026-01-01")
        yield PayrollService(PayrollRepository(manager), users)
        manager.close_all()


@pytest.fixture
def flow_data():
    return {}


class TestOfficer:
    async def test_build_add_view_export(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await payroll_handlers.payroll_build_command(
            make_update(222, "/payroll_build 2026-09"), ctx)
        await payroll_handlers.payroll_add_command(
            make_update(222, "/payroll_add 2026-09 111 10 2000 500"), ctx)
        update = make_update(222, "/payroll_view 2026-09")
        await payroll_handlers.payroll_view_command(update, ctx)
        assert update.effective_message.reply_text.call_count == 1
        await payroll_handlers.payroll_export_command(
            make_update(222, "/payroll_export 2026-09"), ctx)
        run = service._repo.get_by_period("2026-09")
        assert run.status == PayrollRunStatus.EXPORTED

    async def test_double_export_refused(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await payroll_handlers.payroll_build_command(
            make_update(222, "/payroll_build 2026-09"), ctx)
        await payroll_handlers.payroll_export_command(
            make_update(222, "/payroll_export 2026-09"), ctx)
        update = make_update(222, "/payroll_export 2026-09")
        await payroll_handlers.payroll_export_command(update, ctx)
        update.effective_message.reply_text.assert_called_once()
        assert "exported" in update.effective_message.reply_text.call_args[0][0]

    async def test_officer_gate(self, service, flow_data):
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_update(111, "/payroll_build 2026-09")
        await payroll_handlers.payroll_build_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with(
            "Payroll needs HQ rights.")
        assert service._repo.count() == 0

    async def test_add_needs_salary(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await payroll_handlers.payroll_build_command(
            make_update(222, "/payroll_build 2026-09"), ctx)
        update = make_update(222, "/payroll_add 2026-09 999")
        await payroll_handlers.payroll_add_command(update, ctx)
        update.effective_message.reply_text.assert_called_once()
        assert "No salary effective" in \
            update.effective_message.reply_text.call_args[0][0]


class TestSelfService:
    async def _run_with_line(self, service):
        run = service.create_run("2026-09", "222")
        service.add_line(run.id, "111", 12000.0, 10.0, 2000.0, 500.0)
        service.add_line(run.id, "222", 15000.0)

    async def test_mypay_own_line_only(self, service, flow_data):
        await self._run_with_line(service)
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_update(111, "/mypay 2026-09")
        await payroll_handlers.mypay_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "10250" in text and "15000" not in text

    async def test_mypay_none(self, service, flow_data):
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_update(111, "/mypay 2026-09")
        await payroll_handlers.mypay_command(update, ctx)
        assert update.effective_message.reply_text.call_args[0][0] == \
            "No payroll line for you."


class TestSalaries:
    async def test_salary_set(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await payroll_handlers.salary_command(
            make_update(222, "/salary 111 13000"), ctx)
        assert service._users.get_by_chat_id("111").monthly_salary == 13000.0

    async def test_salary_gate(self, service, flow_data):
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_update(111, "/salary 111 13000")
        await payroll_handlers.salary_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with(
            "Payroll needs HQ rights.")

    async def test_csv_paste(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await payroll_handlers.salary_import_command(
            make_update(222, "/salary_import"), ctx)
        assert flow_data["state"] == "awaiting_salary_csv"
        update = make_update(222, "111,14000\n999,5000")
        await payroll_handlers.handle_salary_csv(update, ctx)
        assert flow_data.get("state") is None
        text = update.message.reply_text.call_args[0][0]
        assert "1 updated, 1 unknown" in text
        assert service._users.get_by_chat_id("111").monthly_salary == 14000.0
