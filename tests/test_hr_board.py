"""HR site board tests (006-E; Phase 1: real AuthorizationService stack)."""

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
from app.repositories.membership_repository import MembershipRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.user_repository import UserRepository
from app.services.authorization_service import AuthorizationService

OPERATOR = "999"   # superadmin chat id in this fixture


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
    users = UserRepository(manager)
    members = MembershipRepository(manager)
    auth = AuthorizationService(user_repo=users, super_admin_chat_id=OPERATOR,
                                admin_chat_ids=[], membership_repo=members)
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot_data = {
        "authorization_service": auth,
        "report_repository": ReportRepository(manager),
        "user_repository": users,
        "app_config": config,
    }
    ctx.user_data = {}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    members.grant(OPERATOR, "default", [])   # origin-site membership for gates
    yield {"ctx": ctx, "auth": auth, "manager": manager, "members": members,
           "reports": ctx.bot_data["report_repository"], "users": users}
    manager.close_all()


def _role(ctx, role):
    """Give the OPERATOR this role (superadmin shortcut aside when applicable)."""
    ctx.bot_data["user_repository"].upsert(
        User(chat_id=OPERATOR, role=role, site_id="default"))


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
        assert "Pick a site" in upd.effective_message.reply_text.call_args[0][0]

    async def test_sites_refused_for_normal(self, env):
        _role(env["ctx"], "normal_user")
        upd = make_update(111, text="/hr_sites")
        upd.effective_user.id = 111
        env["members"].grant("111", "default", [])
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

    async def test_site_rejects_unknown_site(self, env):
        _role(env["ctx"], "hr")
        upd = make_update(999, callback_data="hr_site:evil")
        await hr_handlers.handle_hr_callback(upd, env["ctx"])
        assert upd.callback_query.edit_message_text.call_args[0][0] == \
            "Unknown site."

    async def test_print_queues_pdf(self, env):
        _role(env["ctx"], "hr")
        env["reports"].add(_report("main"))
        upd = make_update(999, callback_data=f"hr_print:{date.today().isoformat()}:main")
        await hr_handlers.handle_hr_callback(upd, env["ctx"])
        text = upd.callback_query.edit_message_text.call_args[0][0]
        assert "Queued for print" in text

    async def test_notify_targets_memberships_only(self, env):
        from app.repositories.notification_repository import (
            NotificationRepository)
        from app.services.notification_outbox import NotificationOutbox

        _role(env["ctx"], "hr")
        users = env["users"]
        users.upsert(_user("111", "normal_user", "main"))
        users.upsert(_user("112", "normal_user", "main"))
        users.upsert(_user("114", "normal_user", "hq"))
        users.upsert(_user("115", "normal_user", "main"))   # legacy tag, no membership
        env["members"].grant("111", "main", [])
        env["members"].grant("112", "main", [])
        env["members"].grant("114", "hq", [])
        env["ctx"].bot_data["notification_outbox"] = NotificationOutbox(
            NotificationRepository(env["manager"]))
        upd = make_update(999, callback_data="hr_notify:main")
        await hr_handlers.handle_hr_callback(upd, env["ctx"])
        sent_to = sorted(
            call.kwargs["chat_id"]
            for call in env["ctx"].bot.send_message.call_args_list
        )
        assert sent_to == [111, 112]          # 115 legacy-tagged excluded
        text = upd.callback_query.edit_message_text.call_args[0][0]
        assert "Queued 2 reminder" in text
        # durable: one row per recipient, deduped on repeat
        outbox = env["ctx"].bot_data["notification_outbox"]
        assert outbox.pending_count("main") == 0  # inline dispatch sent them
        sent_rows = [outbox._repo.get_by_dedup(
            f"main|{date.today().isoformat()}|{c}|report_missing|"
            f"missing:{date.today().isoformat()}|0")
            for c in ("111", "112")]
        assert all(r is not None and r.status == "sent" for r in sent_rows)
        await hr_handlers.handle_hr_callback(
            make_update(999, callback_data="hr_notify:main"), env["ctx"])
        assert outbox.pending_count("main") == 0  # repeat sends nothing new

    async def test_print_all(self, env):
        _role(env["ctx"], "hr")
        env["reports"].add(_report("main"))
        upd = make_update(999, text="/hr_print_all")
        await hr_handlers.print_all_command(upd, env["ctx"])
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "Main Site" in text and "Headquarters" in text

    async def test_member_grant_validates_site(self, env):
        _role(env["ctx"], "hr")
        upd = make_update(999, text="/hr_member grant 111 main")
        upd.message.text = "/hr_member grant 111 main"
        await hr_handlers.member_command(upd, env["ctx"])
        assert env["members"].find("111", "main") is not None
        bad = make_update(999, text="/hr_member grant 111 nowhere")
        bad.message.text = "/hr_member grant 111 nowhere"
        await hr_handlers.member_command(bad, env["ctx"])
        assert "Unknown site" in bad.effective_message.reply_text.call_args[0][0]

    async def test_member_non_superadmin_refused(self, env):
        upd = make_update(111, text="/hr_member grant 112 main")
        upd.effective_user.id = 111
        upd.message.text = "/hr_member grant 112 main"
        await hr_handlers.member_command(upd, env["ctx"])
        assert "superadmin" in upd.effective_message.reply_text.call_args[0][0]
        assert env["members"].find("112", "main") is None
