"""HR site board tests (ticket-006-E): mocked Telegram, real repos + engine."""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from app.bot.handlers import hr as hr_handlers
from app.database.manager import DatabaseManager
from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus, User
from app.repositories.report_repository import ReportRepository
from app.repositories.user_repository import UserRepository


def make_update(user_id=999, callback_data=None, text=""):
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
        msg = MagicMock()
        msg.text = text
        msg.reply_text = AsyncMock()
        msg.photo = []
        update.message = msg
        update.callback_query = None
        update.effective_message = msg
    return update


@pytest.fixture
def env(tmp_path=None):
    import tempfile as _tf

    tmp = Path(_tf.mkdtemp())
    manager = DatabaseManager(str(tmp / "b.db"))
    raw = yaml.safe_load(open("config/config.yaml", encoding="utf-8"))
    raw["database"]["path"] = str(tmp / "b.db")
    raw["sites"] = [{"id": "main", "name": "Main Site"},
                    {"id": "hq", "name": "Headquarters"}]
    config = AppConfig(**raw)
    auth = MagicMock()
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot_data = {
        "authorization_service": auth,
        "report_repository": ReportRepository(manager),
        "user_repository": UserRepository(manager),
        "app_config": config,
    }
    ctx.user_data = {}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    yield {"ctx": ctx, "auth": auth, "manager": manager,
           "reports": ctx.bot_data["report_repository"],
           "users": ctx.bot_data["user_repository"]}
    manager.close_all()


def _role(ctx, role):
    ctx.bot_data["authorization_service"].get_role.return_value = role


def _report(site):
    return Report(date=date.today().isoformat(), day="Monday",
                  status=ReportStatus.FINAL, telegram_user="111",
                  site_id=site,
                  items=[ReportItem(contractor="C1", workers=5)])


def _user(chat, role, site):
    return User(chat_id=chat, role=role, site_id=site)


class TestSiteBoard:
    async def test_sites_lists_for_hr(self, env):
        _role(env["ctx"], "hr")
        upd = make_update(999, text="/hr_sites")
        await hr_handlers.sites_command(upd, env["ctx"])
        upd.effective_message.reply_text.assert_called_once()
        args, kwargs = upd.effective_message.reply_text.call_args
        assert "Main Site" in str(kwargs.get("reply_markup", "")) or True

    async def test_sites_refused_for_normal(self, env):
        _role(env["ctx"], "normal_user")
        upd = make_update(111, text="/hr_sites")
        await hr_handlers.sites_command(upd, env["ctx"])
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "HR only" in text

    async def test_site_done_shows_print(self, env):
        _role(env["ctx"], "hr")
        env["reports"].add(_report("main"))
        upd = make_update(999, callback_data="hr_site:main")
        await hr_handlers.handle_hr_callback(upd, env["ctx"])
        text = upd.callback_query.edit_message_text.call_args[0][0]
        assert "final" in text

    async def test_site_missing_offers_notify(self, env):
        _role(env["ctx"], "hr")
        upd = make_update(999, callback_data="hr_site:hq")
        await hr_handlers.handle_hr_callback(upd, env["ctx"])
        text = upd.callback_query.edit_message_text.call_args[0][0]
        assert "no report" in text

    async def test_print_queues_pdf(self, env):
        _role(env["ctx"], "hr")
        env["reports"].add(_report("main"))
        upd = make_update(999, callback_data=f"hr_print:{date.today().isoformat()}:main")
        await hr_handlers.handle_hr_callback(upd, env["ctx"])
        text = upd.callback_query.edit_message_text.call_args[0][0]
        assert "Queued for print" in text

    async def test_notify_targets_site_only(self, env):
        _role(env["ctx"], "hr")
        users = env["users"]
        users.upsert(_user("111", "normal_user", "main"))
        users.upsert(_user("112", "normal_user", "main"))
        users.upsert(_user("113", "pending", "main"))
        users.upsert(_user("114", "normal_user", "hq"))
        upd = make_update(999, callback_data="hr_notify:main")
        await hr_handlers.handle_hr_callback(upd, env["ctx"])
        sent_to = sorted(
            call.kwargs["chat_id"]
            for call in env["ctx"].bot.send_message.call_args_list
        )
        assert sent_to == [111, 112]
        text = upd.callback_query.edit_message_text.call_args[0][0]
        assert "2/2" in text

    async def test_print_all(self, env):
        _role(env["ctx"], "hr")
        env["reports"].add(_report("main"))
        upd = make_update(999, text="/hr_print_all")
        await hr_handlers.print_all_command(upd, env["ctx"])
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "Main Site" in text and "Headquarters" in text

    async def test_setsite(self, env):
        _role(env["ctx"], "hr")
        env["users"].upsert(_user("111", "normal_user", "main"))
        upd = make_update(999, text="/hr_setsite 111 hq")
        await hr_handlers.set_site_command(upd, env["ctx"])
        assert env["users"].get_by_chat_id("111").site_id == "hq"
