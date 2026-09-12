"""Report review handler flows (ticket-027): mocked Telegram, real stack."""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import start as start_handlers
from app.database.manager import DatabaseManager
from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.report_repository import ReportRepository
from app.services.event_log_service import EventLogService
from app.services.report_workflow_service import ReportWorkflowService

SITE = "default"
TODAY = date.today().isoformat()


def make_update(user_id=222, text="", callback_data=None):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = f"User{user_id}"
    update.effective_user.last_name = ""
    if callback_data is not None:
        query = MagicMock(spec=CallbackQuery)
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
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


def make_auth(role="project_manager"):
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session=None: session or SITE)
    auth.sites_for_user.return_value = [SITE]
    auth.can_finalize.side_effect = lambda chat_id: role in (
        "superadmin", "project_manager", "executive_engineer", "admin",
        "normal_user")
    auth.is_admin.side_effect = lambda chat_id: role in (
        "superadmin", "admin")
    return auth


def make_config():
    return AppConfig(**{
        "template": {"file": "t.ots", "tables_file": "t.ods"},
        "date": {"cell": "B7", "day_cell": "B5"},
        "table": {"start_row": 11, "columns": {
            "serial": "B", "contractor": "C", "type": "D",
            "zone": "E", "workers": "F", "details": "G"}},
        "output": {"pdf_folder": "x", "docs_folder": "y"},
        "database": {"path": ":memory:"},
        "tables": {"contractor_sheet": "c", "zones_sheet": "z"},
        "lifecycle": {"auto_lock_hours": 24, "max_versions": 50},
    })


@pytest.fixture
def stack():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "r.db"))
        repo = ReportRepository(manager, EventLogRepository(manager))
        wf = ReportWorkflowService(repo, EventLogService(manager), make_config())
        yield {"manager": manager, "repo": repo, "wf": wf}
        manager.close_all()


def make_context(auth, stack, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {
        "authorization_service": auth,
        "report_repository": stack["repo"],
        "workflow_service": stack["wf"],
    }
    context.user_data = user_data if user_data is not None else {}
    context.args = []
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


def _final(stack, by="111"):
    rep = Report(date=TODAY, day="Mon", status=ReportStatus.DRAFT,
                 telegram_user=by, site_id=SITE,
                 items=[ReportItem(contractor="C1", workers=5)])
    repo = stack["repo"]
    repo.add(rep)
    return stack["wf"].finalize_report(
        repo.get_by_date(TODAY, site_id=SITE), by)


class TestApprove:
    async def test_approve_flow(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        await start_handlers.approve_command(make_update(222, "/approve ok"), ctx)
        got = stack["repo"].get_by_date(TODAY, site_id=SITE)
        assert got.status == ReportStatus.APPROVED
        assert got.approved_by == "222"

    async def test_approve_denied_for_creator_role(self, stack):
        ctx = make_context(make_auth("normal_user"), stack, {})
        _final(stack)
        upd = make_update(111, "/approve")
        await start_handlers.approve_command(upd, ctx)
        assert "reviewer grant" in upd.effective_message.reply_text.call_args[0][0]
        assert stack["repo"].get_by_date(TODAY, site_id=SITE).status == \
            ReportStatus.FINAL

    async def test_approve_non_final_refused(self, stack):
        ctx = make_context(make_auth(), stack, {})
        rep = Report(date=TODAY, day="Mon", status=ReportStatus.DRAFT,
                     telegram_user="111", site_id=SITE,
                     items=[ReportItem(contractor="C1", workers=5)])
        stack["repo"].add(rep)
        upd = make_update(222, "/approve")
        await start_handlers.approve_command(upd, ctx)
        assert "Could not approve" in \
            upd.effective_message.reply_text.call_args[0][0]

    async def test_approve_other_site_denied(self, stack):
        ctx = make_context(make_auth(), stack, {})
        other = Report(date=TODAY, day="Mon", status=ReportStatus.FINAL,
                       telegram_user="999", site_id="other",
                       items=[ReportItem(contractor="C9", workers=1)])
        stack["repo"].add(other)
        upd = make_update(222, "/approve")
        await start_handlers.approve_command(upd, ctx)
        assert "No report found" in \
            upd.effective_message.reply_text.call_args[0][0]
        assert stack["repo"].get_by_date(TODAY, site_id="other").status == \
            ReportStatus.FINAL

    async def test_creator_cannot_self_approve_via_command(self, stack):
        ctx = make_context(make_auth("project_manager"), stack, {})
        _final(stack, by="222")
        upd = make_update(222, "/approve")
        await start_handlers.approve_command(upd, ctx)
        assert "own report" in \
            upd.effective_message.reply_text.call_args[0][0].lower()
        assert stack["repo"].get_by_date(TODAY, site_id=SITE).status == \
            ReportStatus.FINAL

    async def test_creator_cannot_self_reject_via_command(self, stack):
        ctx = make_context(make_auth("project_manager"), stack, {})
        _final(stack, by="222")
        upd = make_update(222, "/reject looks bad")
        await start_handlers.reject_command(upd, ctx)
        assert "own report" in \
            upd.effective_message.reply_text.call_args[0][0].lower()
        assert stack["repo"].get_by_date(TODAY, site_id=SITE).status == \
            ReportStatus.FINAL


class TestRejectResubmit:
    async def test_reject_needs_note(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        upd = make_update(222, "/reject")
        await start_handlers.reject_command(upd, ctx)
        assert "Usage" in upd.effective_message.reply_text.call_args[0][0]

    async def test_reject_flow(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        await start_handlers.reject_command(
            make_update(222, "/reject zone B missing"), ctx)
        got = stack["repo"].get_by_date(TODAY, site_id=SITE)
        assert got.status == ReportStatus.REJECTED
        assert got.reject_note == "zone B missing"

    async def test_reject_via_button_note_state(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        upd = make_update(222, callback_data="reject_report")
        await start_handlers.handle_main_menu_callback(upd, ctx)
        assert ctx.user_data["state"] == "awaiting_report_reject_note"
        note = make_update(222, "fix the zone please")
        await start_handlers.handle_report_reject_note(note, ctx)
        got = stack["repo"].get_by_date(TODAY, site_id=SITE)
        assert got.status == ReportStatus.REJECTED
        assert "state" not in ctx.user_data

    async def test_resubmit_by_creator(self, stack):
        ctx = make_context(make_auth("normal_user"), stack, {})
        _final(stack)
        await start_handlers.reject_command(
            make_update(222, "/reject zone missing"),
            make_context(make_auth(), stack, {}))
        upd = make_update(111, "/resubmit")
        await start_handlers.resubmit_command(upd, ctx)
        assert stack["repo"].get_by_date(TODAY, site_id=SITE).status == \
            ReportStatus.DRAFT

    async def test_resubmit_stranger_denied(self, stack):
        ctx = make_context(make_auth("normal_user"), stack, {})
        _final(stack)
        stack["wf"].reject_report(
            stack["repo"].get_by_date(TODAY, site_id=SITE), "222", "x")
        upd = make_update(777, "/resubmit")
        await start_handlers.resubmit_command(upd, ctx)
        assert "creator or a reviewer" in \
            upd.effective_message.reply_text.call_args[0][0]


class TestLockAndView:
    async def test_lock_requires_approval(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        upd = make_update(222, "/lock")
        await start_handlers.lock_command(upd, ctx)
        assert "approved" in upd.effective_message.reply_text.call_args[0][0]
        assert stack["repo"].get_by_date(TODAY, site_id=SITE).status == \
            ReportStatus.FINAL

    async def test_lock_after_approval(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        await start_handlers.approve_command(make_update(222, "/approve"), ctx)
        upd = make_update(222, "/lock")
        await start_handlers.lock_command(upd, ctx)
        assert ctx.user_data["state"] == "awaiting_lock_confirmation"
        assert "Are you sure" in \
            upd.effective_message.reply_text.call_args[0][0]

    async def test_view_shows_stamps(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        await start_handlers.approve_command(make_update(222, "/approve"), ctx)
        upd = make_update(222, "/view")
        await start_handlers.view_command(upd, ctx)
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "approved" in text and "222" in text

    async def test_menu_approve_button(self, stack):
        ctx = make_context(make_auth(), stack, {})
        _final(stack)
        upd = make_update(222, callback_data="approve_report")
        await start_handlers.handle_main_menu_callback(upd, ctx)
        assert stack["repo"].get_by_date(TODAY, site_id=SITE).status == \
            ReportStatus.APPROVED

    async def test_keyboard_reviewer_buttons(self, stack):
        from app.bot.keyboards import report_actions_keyboard

        kb = report_actions_keyboard("final", role="project_manager")
        flat = str(kb.to_dict())
        assert "Approve" in flat and "Reject" in flat
        kb2 = report_actions_keyboard("final", role="normal_user")
        assert "Approve" not in str(kb2.to_dict())
        kb3 = report_actions_keyboard("approved", role="admin")
        assert "Lock" in str(kb3.to_dict())
        kb4 = report_actions_keyboard("rejected", role="normal_user")
        assert "Resubmit" in str(kb4.to_dict())
