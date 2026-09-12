"""Case handler flows (ticket-015): mocked Telegram, real case engine."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import cases as case_handlers
from app.database.manager import DatabaseManager
from app.models.case import CaseStatus, CaseType
from app.repositories.case_repository import CaseRepository
from app.services.case_service import CaseService


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
    auth.resolve_active_site.side_effect = (lambda chat_id, session_site=None: session_site or 'default')
    auth.sites_for_user.return_value = ['default']
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    return auth


def make_context(auth, service, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth, "case_service": service}
    context.user_data = user_data if user_data is not None else {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "c.db"))
        yield CaseService(CaseRepository(manager))
        manager.close_all()


@pytest.fixture
def flow_data():
    return {}


class TestFiling:
    async def test_grievance_flow(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await case_handlers.grievance_command(make_update(111, "/grievance"), ctx)
        assert flow_data["state"] == "awaiting_case_summary"
        await case_handlers.handle_case_summary(make_update(111, "cold showers"), ctx)
        assert flow_data.get("state") is None
        rows = service._repo.for_reporter("111")
        assert len(rows) == 1
        assert rows[0].case_type == CaseType.GRIEVANCE
        assert rows[0].status == CaseStatus.FILED

    async def test_viewer_denied(self, service, flow_data):
        ctx = make_context(make_auth("viewer"), service, flow_data)
        await case_handlers.grievance_command(make_update(111, "/grievance"), ctx)
        assert "state" not in flow_data
        assert service._repo.count() == 0

    async def test_blank_summary_reprompt(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await case_handlers.suggestion_command(make_update(111, "/suggestion"), ctx)
        await case_handlers.handle_case_summary(make_update(111, "   "), ctx)
        assert flow_data["state"] == "awaiting_case_summary"


class TestQueues:
    async def test_mycases_empty(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        update = make_update(111, "/mycases")
        await case_handlers.mycases_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with("You have no cases.")

    async def test_cases_denied_for_normal(self, service, flow_data):
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_update(111, "/cases")
        await case_handlers.cases_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with(
            "Review queue is for reviewers only.")

    async def test_cases_visible_to_hr(self, service, flow_data):
        service.file("111", CaseType.COMPLAINT, "food", site_id="default")
        ctx = make_context(make_auth("hr"), service, flow_data)
        update = make_update(222, "/cases")
        await case_handlers.cases_command(update, ctx)
        assert update.effective_message.reply_text.call_count == 1


class TestReviewResolve:
    def _filed_id(self, service):
        return service.file("111", CaseType.GRIEVANCE, "cold",
                            site_id="default").id

    async def test_review_callback(self, service, flow_data):
        case_id = self._filed_id(service)
        ctx = make_context(make_auth("hr"), service, flow_data)
        update = make_callback(222, f"case_review:{case_id}")
        await case_handlers.handle_case_callback(update, ctx)
        assert service._repo.get_by_id(case_id).status == CaseStatus.UNDER_REVIEW
        update.callback_query.edit_message_text.assert_called_once()

    async def test_review_denied_for_normal(self, service, flow_data):
        case_id = self._filed_id(service)
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_callback(111, f"case_review:{case_id}")
        await case_handlers.handle_case_callback(update, ctx)
        assert service._repo.get_by_id(case_id).status == CaseStatus.FILED
        update.callback_query.edit_message_text.assert_called_once_with(
            "Review needs a reviewer account.")

    async def test_resolve_needs_note_state(self, service, flow_data):
        case_id = self._filed_id(service)
        service.review(case_id, "222", site_id="default")
        ctx = make_context(make_auth("hr"), service, flow_data)
        await case_handlers.handle_case_callback(
            make_callback(222, f"case_resolve:{case_id}"), ctx)
        assert flow_data["state"] == "awaiting_case_note"
        await case_handlers.handle_case_note(make_update(222, "heaters ordered"), ctx)
        assert service._repo.get_by_id(case_id).status == CaseStatus.RESOLVED
        ctx.bot.send_message.assert_called_once()  # reporter notified


class TestAppeal:
    def _resolved_id(self, service):
        case = service.file("111", CaseType.SUGGESTION, "more tea",
                            site_id="default")
        service.review(case.id, "222", site_id="default")
        return service.resolve(case.id, "222", "done", site_id="default").id

    async def test_appeal_flow(self, service, flow_data):
        case_id = self._resolved_id(service)
        ctx = make_context(make_auth(), service, flow_data)
        await case_handlers.handle_case_callback(
            make_callback(111, f"case_appeal:{case_id}"), ctx)
        assert flow_data["state"] == "awaiting_case_appeal"
        await case_handlers.handle_case_appeal(make_update(111, "still bad"), ctx)
        assert service._repo.get_by_id(case_id).status == CaseStatus.APPEALED

    async def test_appeal_denied_for_stranger(self, service, flow_data):
        case_id = self._resolved_id(service)
        ctx = make_context(make_auth(), service, flow_data)
        update = make_callback(999, f"case_appeal:{case_id}")
        await case_handlers.handle_case_callback(update, ctx)
        update.callback_query.edit_message_text.assert_called_once_with(
            "Only the filer may appeal this case.")
        assert service._repo.get_by_id(case_id).status == CaseStatus.RESOLVED
