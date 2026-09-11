"""Discipline handler flows (ticket-018): mocked Telegram, real engine."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import discipline as disc_handlers
from app.database.manager import DatabaseManager
from app.models.discipline import DisciplineStatus
from app.repositories.discipline_repository import DisciplineRepository
from app.services.discipline_service import DisciplineService


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


def make_auth(role="hr"):
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    return auth


def make_context(auth, service, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth,
                        "discipline_service": service}
    context.user_data = user_data if user_data is not None else {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "d.db"))
        yield DisciplineService(DisciplineRepository(manager))
        manager.close_all()


@pytest.fixture
def flow_data():
    return {}


class TestFiling:
    async def test_file_single_shot(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        await disc_handlers.discipline_command(
            make_update(222, "/discipline 111 late again"), ctx)
        rows = service._repo.for_subject("111")
        assert len(rows) == 1
        assert rows[0].status == DisciplineStatus.FILED

    async def test_normal_user_denied(self, service, flow_data):
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_update(111, "/discipline 222 x")
        await disc_handlers.discipline_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with(
            "Discipline filing is HQ-only.")
        assert service._repo.count() == 0

    async def test_self_filing_refused(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        update = make_update(222, "/discipline 222 myself")
        await disc_handlers.discipline_command(update, ctx)
        update.effective_message.reply_text.assert_called_once()
        assert service._repo.count() == 0

    async def test_usage_error(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data)
        update = make_update(222, "/discipline")
        await disc_handlers.discipline_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with(
            "Usage: /discipline <subject_chat_id> <summary>")


class TestQueue:
    async def test_queue_denied_for_normal(self, service, flow_data):
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_update(111, "/discipline_queue")
        await disc_handlers.discipline_queue_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with(
            "Discipline queue is HQ-only.")

    async def test_queue_visible_to_hq(self, service, flow_data):
        service.file("111", "222", "late", site_id="default")
        ctx = make_context(make_auth(), service, flow_data)
        update = make_update(222, "/discipline_queue")
        await disc_handlers.discipline_queue_command(update, ctx)
        assert update.effective_message.reply_text.call_count == 1


class TestDecide:
    def _reviewed_id(self, service):
        case = service.file("111", "222", "late", site_id="default")
        return service.review(case.id, "333", site_id="default").id

    async def test_review_then_decide(self, service, flow_data):
        case = service.file("111", "222", "late", site_id="default")
        ctx = make_context(make_auth(), service, flow_data)
        await disc_handlers.handle_discipline_callback(
            make_callback(333, f"disc_review:{case.id}"), ctx)
        assert service._repo.get_by_id(case.id).status == \
            DisciplineStatus.UNDER_REVIEW
        await disc_handlers.handle_discipline_callback(
            make_callback(333, f"disc_decide:{case.id}"), ctx)
        assert flow_data["state"] == "awaiting_disc_note"
        await disc_handlers.handle_disc_note(
            make_update(333, "written warning | third late arrival"), ctx)
        decided = service._repo.get_by_id(case.id)
        assert decided.status == DisciplineStatus.DECIDED
        assert decided.decision == "written warning"
        ctx.bot.send_message.assert_called_once()  # subject notified

    async def test_filer_decide_refused(self, service, flow_data):
        case_id = self._reviewed_id(service)
        ctx = make_context(make_auth(), service, flow_data)
        update = make_callback(222, f"disc_decide:{case_id}")
        await disc_handlers.handle_discipline_callback(update, ctx)
        update.callback_query.edit_message_text.assert_called_once_with(
            "Filer cannot decide their own filing.")

    async def test_note_format_reprompt(self, service, flow_data):
        flow_data.update({"disc_decide_id": 1, "state": "awaiting_disc_note"})
        ctx = make_context(make_auth(), service, flow_data)
        update = make_update(333, "no separator here")
        await disc_handlers.handle_disc_note(update, ctx)
        update.message.reply_text.assert_called_once()
        assert flow_data["state"] == "awaiting_disc_note"


class TestAppeal:
    def _decided_id(self, service):
        case = service.file("111", "222", "late", site_id="default")
        service.review(case.id, "333", site_id="default")
        return service.decide(case.id, "333", "warning", "note",
                              site_id="default").id

    async def test_subject_appeal(self, service, flow_data):
        case_id = self._decided_id(service)
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        await disc_handlers.handle_discipline_callback(
            make_callback(111, f"disc_appeal:{case_id}"), ctx)
        assert flow_data["state"] == "awaiting_disc_appeal"
        await disc_handlers.handle_disc_appeal(make_update(111, "bus broke"), ctx)
        assert service._repo.get_by_id(case_id).status == \
            DisciplineStatus.APPEALED

    async def test_stranger_appeal_refused(self, service, flow_data):
        case_id = self._decided_id(service)
        ctx = make_context(make_auth("normal_user"), service, flow_data)
        update = make_callback(999, f"disc_appeal:{case_id}")
        await disc_handlers.handle_discipline_callback(update, ctx)
        update.callback_query.edit_message_text.assert_called_once_with(
            "Only the subject may appeal this case.")
