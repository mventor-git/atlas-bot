"""Report visibility matrix (ticket-029, Stage 2): same record, two
representations; files obey the boundary; tampering denies."""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import start as start_handlers
from app.bot.handlers import search as search_handlers
from app.bot.handlers import comparison as compare_handlers
from app.bot.handlers import report_retrieval as retrieval_handlers
from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services import report_visibility as visibility

SITE = "default"
TODAY = date.today().isoformat()


def make_auth(role="project_manager", sites=None):
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session=None: session or SITE)
    auth.sites_for_user.return_value = sites if sites is not None else [SITE]
    return auth


def make_update(user_id=111, text="", callback_data=None):
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
        msg.reply_document = AsyncMock()
        msg.photo = []
        update.message = msg
        update.callback_query = None
        update.effective_message = msg
    return update


def make_context(auth, repo, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth,
                        "report_repository": repo}
    context.user_data = {} if user_data is None else user_data
    context.args = []
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.bot.send_document = AsyncMock()
    return context


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "r.db"))
        yield ReportRepository(manager)
        manager.close_all()


def _report(site=SITE, status=ReportStatus.FINAL, by="creator-1"):
    return Report(date=TODAY, day="Mon", status=status, telegram_user=by,
                  site_id=site,
                  items=[ReportItem(contractor="SecretCo", type="Civil",
                                    zone="Zone9", workers=10, craftsmen=6,
                                    helpers=4, details="sensitive notes")])


class TestResolve:
    def test_hq_anywhere_sees_detail_cross_site(self):
        auth = make_auth("hr", sites=["hq"])
        assert visibility.resolve(auth, "1", "site-a") == visibility.HQ

    def test_hq_cap_wins_over_site(self):
        auth = make_auth("project_manager", sites=["site-a"])
        assert visibility.resolve(auth, "1", "site-a") == visibility.HQ

    def test_site_member_with_create_sees_detail_own_site(self):
        auth = make_auth("normal_user", sites=["site-a"])
        assert visibility.resolve(auth, "1", "site-a") == visibility.SITE

    def test_member_without_caps_is_owner(self):
        auth = make_auth("viewer", sites=["site-a"])
        assert visibility.resolve(auth, "1", "site-a") == visibility.OWNER

    def test_non_member_denied(self):
        auth = make_auth("viewer", sites=["other"])
        assert visibility.resolve(auth, "1", "site-a") == visibility.NONE

    def test_auth_failure_denies(self):
        auth = MagicMock()
        auth.sites_for_user.side_effect = RuntimeError("db down")
        assert visibility.resolve(auth, "1", "site-a") == visibility.NONE

    def test_owner_status_gate(self):
        assert visibility.owner_may_see_status("final") is True
        assert visibility.owner_may_see_status("approved") is True
        assert visibility.owner_may_see_status("locked") is True
        assert visibility.owner_may_see_status("no_report") is True
        assert visibility.owner_may_see_status("draft") is False
        assert visibility.owner_may_see_status(None) is False


class TestRenderSimple:
    def test_no_sensitive_fields(self, repo):
        repo.add(_report())
        rep = repo.get_by_date(TODAY, site_id=SITE)
        text = visibility.render_simple(rep)
        for secret in ("SecretCo", "Zone9", "sensitive notes", "creator-1",
                       "Civil"):
            assert secret not in text
        for visible in (TODAY, "10", "final"):
            assert visible in text


class TestView:
    async def test_owner_gets_simple(self, repo):
        repo.add(_report())
        ctx = make_context(make_auth("viewer"), repo)
        upd = make_update(111, "/view")
        await start_handlers.view_command(upd, ctx)
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "SecretCo" not in text and "Total Workers: 10" in text

    async def test_owner_denied_draft(self, repo):
        rep = _report(status=ReportStatus.DRAFT)
        repo.add(rep)
        ctx = make_context(make_auth("viewer"), repo)
        upd = make_update(111, "/view")
        await start_handlers.view_command(upd, ctx)
        assert "not yet available" in \
            upd.effective_message.reply_text.call_args[0][0]

    async def test_reviewer_gets_detail(self, repo):
        repo.add(_report())
        ctx = make_context(make_auth("project_manager"), repo)
        upd = make_update(222, "/view")
        await start_handlers.view_command(upd, ctx)
        assert "SecretCo" in upd.effective_message.reply_text.call_args[0][0]


class TestFiles:
    def _with_pdf(self, repo, tmp_path):
        pdf = tmp_path / "r.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        rep = _report()
        rep.preview_pdf_path = str(pdf)
        repo.add(rep)

    async def test_owner_no_pdf(self, repo, tmp_path):
        self._with_pdf(repo, tmp_path)
        ctx = make_context(make_auth("viewer"), repo)
        ctx.args = [TODAY]
        upd = make_update(111, f"/get {TODAY}")
        await retrieval_handlers.get_report_command(upd, ctx)
        upd.message.reply_document.assert_not_called()
        text = upd.message.reply_text.call_args[0][0]
        assert "reviewer access" in text and "SecretCo" not in text

    async def test_reviewer_gets_pdf(self, repo, tmp_path):
        self._with_pdf(repo, tmp_path)
        ctx = make_context(make_auth("project_manager"), repo)
        ctx.args = [TODAY]
        upd = make_update(222, f"/get {TODAY}")
        await retrieval_handlers.get_report_command(upd, ctx)
        upd.message.reply_document.assert_called_once()


class TestSearchDetail:
    async def test_owner_detail_simple_no_pdf(self, repo):
        repo.add(_report())
        rep = repo.get_by_date(TODAY, site_id=SITE)
        ctx = make_context(make_auth("viewer"), repo)
        upd = make_update(111)
        await search_handlers._show_report_detail(upd, ctx, rep)
        upd.message.reply_document.assert_not_called()
        assert "SecretCo" not in upd.message.reply_text.call_args[0][0]

    async def test_tampered_other_site_denied(self, repo):
        other = _report(site="other")
        repo.add(other)
        ctx = make_context(make_auth("project_manager"), repo)
        upd = make_update(222, callback_data=f"view_report:{TODAY}")
        await search_handlers.handle_view_report_callback(upd, ctx)
        assert "No report found" in \
            upd.callback_query.edit_message_text.call_args[0][0]

    def test_search_list_leaks_no_names(self):
        from app.bot.handlers.search import _format_search_hits

        hits = [MagicMock(date=TODAY, contractor_count=2,
                          status=ReportStatus.FINAL)]
        formatted = _format_search_hits(hits)
        assert all("contractor" not in k.lower() or k == "contractor_count"
                   for hit in formatted for k in hit)
        blob = str(formatted)
        assert "SecretCo" not in blob


class TestAnalyticalTools:
    async def test_comparison_denied_for_owner(self, repo):
        ctx = make_context(make_auth("viewer"), repo)
        upd = make_update(111, "/compare")
        await compare_handlers.compare_command(upd, ctx)
        assert "reviewer access" in \
            upd.effective_message.reply_text.call_args[0][0]
        assert "state" not in ctx.user_data

    async def test_contractor_history_denied_for_owner(self, repo):
        ctx = make_context(make_auth("viewer"), repo)
        upd = make_update(111, callback_data="report_period:this_week")
        await start_handlers.handle_contractor_report_period(upd, ctx)
        assert "reviewer access" in \
            upd.callback_query.edit_message_text.call_args[0][0]
