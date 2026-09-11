"""Flash SELF-scope enforcement tests (ticket-012, P1-2).

Pending/rejected/unknown callers must be denied on every read surface;
viewers pass through; help stays open.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import comparison as comparison_handlers
from app.bot.handlers import report_retrieval as retrieval_handlers
from app.bot.handlers import search as search_handlers
from app.bot.handlers import start as start_handlers


def make_message_update(user_id=111, text=""):
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


def make_callback_update(user_id=111, data=""):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = f"User{user_id}"
    update.effective_user.last_name = ""
    cq = MagicMock(spec=CallbackQuery)
    cq.data = data
    cq.answer = AsyncMock()
    cq.edit_message_text = AsyncMock()
    update.callback_query = cq
    update.message = None
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


def make_context(role="viewer", extra_bot_data=None, user_data=None):
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.can_view_reports.return_value = role not in ("pending", "rejected", "unknown")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_data = {"authorization_service": auth}
    bot_data.update(extra_bot_data or {})
    context.bot_data = bot_data
    context.user_data = dict(user_data or {})
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.args = []
    return context


def _denied_text(calls):
    return " ".join(str(call.args[0]) for call in calls if call.args)


def _is_denied(text: str) -> bool:
    return ("permissions don't include" in text
            or "awaiting approval" in text)


class TestMenuQuarantine:
    async def test_pending_denied_all_but_help(self):
        repo = MagicMock()
        ctx = make_context("pending", {"report_repository": repo})
        upd = make_callback_update(111, "view_report")
        await start_handlers.handle_main_menu_callback(upd, ctx)
        upd.callback_query.edit_message_text.assert_called_once()
        assert repo.get_by_date.called is False

    async def test_help_open_for_pending(self):
        ctx = make_context("pending", {})
        upd = make_callback_update(111, "help")
        await start_handlers.handle_main_menu_callback(upd, ctx)
        text = _denied_text(upd.callback_query.edit_message_text.call_args_list)
        assert "awaiting approval" not in text and "permissions" not in text

    async def test_pending_gets_soft_denial(self):
        ctx = make_context("pending", {})
        upd = make_message_update(111, "/get 2026-07-12")
        ctx.args = ["2026-07-12"]
        await retrieval_handlers.get_report_command(upd, ctx)
        text = _denied_text(upd.message.reply_text.call_args_list)
        assert "awaiting approval" in text

    async def test_viewer_passes_menu(self):
        repo = MagicMock()
        repo.get_by_date.return_value = None
        ctx = make_context("viewer", {"report_repository": repo})
        upd = make_callback_update(111, "view_report")
        await start_handlers.handle_main_menu_callback(upd, ctx)
        assert repo.get_by_date.called is True


class TestRetrievalGates:
    async def test_get_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_message_update(111, "/get 2026-07-12")
        ctx.args = ["2026-07-12"]
        await retrieval_handlers.get_report_command(upd, ctx)
        text = _denied_text(upd.message.reply_text.call_args_list)
        assert _is_denied(text)

    async def test_get_allowed_viewer(self):
        repo = MagicMock()
        repo.get_by_date.return_value = None
        ctx = make_context("viewer", {"report_repository": repo})
        upd = make_message_update(111, "/get 2026-07-12")
        ctx.args = ["2026-07-12"]
        await retrieval_handlers.get_report_command(upd, ctx)
        assert repo.get_by_date.called is True

    async def test_date_text_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_message_update(111, "2026-07-12")
        handled = await retrieval_handlers.handle_date_text(upd, ctx)
        assert handled is True
        text = _denied_text(upd.message.reply_text.call_args_list)
        assert _is_denied(text)


class TestComparisonGates:
    async def test_compare_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_message_update(111, "/compare")
        await comparison_handlers.compare_command(upd, ctx)
        assert ctx.user_data.get("state") != "awaiting_compare_date_a"

    async def test_compare_text_denied_pending(self):
        ctx = make_context("pending", {}, {"state": "awaiting_compare_date_a"})
        upd = make_message_update(111, "2026-07-10")
        await comparison_handlers.handle_compare_text(upd, ctx)
        assert ctx.user_data.get("state") is None

    async def test_compare_allowed_viewer(self):
        ctx = make_context("viewer", {})
        upd = make_message_update(111, "/compare")
        await comparison_handlers.compare_command(upd, ctx)
        assert ctx.user_data.get("state") == "awaiting_compare_date_a"


class TestSearchGates:
    async def test_search_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_message_update(111, "/search")
        await search_handlers.search_command(upd, ctx)
        assert ctx.user_data.get("state") != "awaiting_search_query"

    async def test_search_query_denied_pending(self):
        ctx = make_context("pending", {}, {"state": "awaiting_search_query"})
        upd = make_message_update(111, "Civil")
        await search_handlers.handle_search_query(upd, ctx)
        assert ctx.user_data.get("state") is None

    async def test_view_callback_denied_pending(self):
        ctx = make_context("pending", {"report_repository": MagicMock()})
        upd = make_callback_update(111, "view_report:2026-07-12")
        await search_handlers.handle_view_report_callback(upd, ctx)
        text = _denied_text(upd.effective_message.reply_text.call_args_list)
        assert _is_denied(text)

    async def test_page_callback_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_callback_update(111, "search_page:2")
        await search_handlers.handle_search_page(upd, ctx)
        text = _denied_text(upd.effective_message.reply_text.call_args_list)
        assert _is_denied(text)


class TestContractorFlowGates:
    async def test_period_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_callback_update(111, "report_period:this_week")
        await start_handlers.handle_contractor_report_period(upd, ctx)
        text = _denied_text(upd.effective_message.reply_text.call_args_list)
        assert _is_denied(text)


class TestCommandGates:
    async def test_preview_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_message_update(111, "/preview")
        await start_handlers.preview_pdf_command(upd, ctx)
        text = _denied_text(upd.message.reply_text.call_args_list)
        assert _is_denied(text)

    async def test_view_denied_pending(self):
        ctx = make_context("pending", {})
        upd = make_message_update(111, "/view")
        await start_handlers.view_command(upd, ctx)
        text = _denied_text(upd.message.reply_text.call_args_list)
        assert _is_denied(text)

    async def test_view_allowed_viewer(self):
        repo = MagicMock()
        repo.get_by_date.return_value = None
        ctx = make_context("viewer", {"report_repository": repo})
        upd = make_message_update(111, "/view")
        await start_handlers.view_command(upd, ctx)
        assert repo.get_by_date.called is True
