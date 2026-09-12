"""site_session tests (Phase 1): validated active-site switching."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import ContextTypes

from app.bot import site_session


def make_update(user_id=111, text="/site"):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    update.callback_query = None
    return update


def make_cb_update(user_id, data):
    update = make_update(user_id)
    query = MagicMock(spec=CallbackQuery)
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def make_auth():
    auth = MagicMock()
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session_site=None: (
            session_site if session_site in ("a", "b")
            else "a" if chat_id == "solo" else None))
    auth.sites_for_user.side_effect = lambda chat_id: (
        ["a"] if chat_id == "solo" else ["a", "b"] if chat_id in ("multi", "111")
        else [])
    return auth


def make_context(auth=None, user_data=None):
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot_data = {"authorization_service": auth or make_auth()}
    ctx.user_data = {} if user_data is None else user_data
    return ctx


class TestPick:
    async def test_single_auto_binds(self):
        ctx = make_context()
        assert site_session.resolve_site(make_update("solo"), ctx) == "a"

    async def test_multi_requires_pick_then_validates(self):
        ctx = make_context()
        assert site_session.resolve_site(make_update(111), ctx) is None
        await site_session.ask_site(make_update(111), ctx, resume="x", hint="go")
        keyboard = ctx.user_data  # side effects
        assert keyboard["site_pick_resume"] == "x"
        # forged site not in memberships -> rejected
        bad = make_cb_update(111, "sitepick:evil")
        await site_session.handle_site_pick(bad, ctx)
        bad.callback_query.edit_message_text.assert_called_once_with(
            "Not a member of that site.")
        assert "active_site" not in ctx.user_data
        ok = make_cb_update(111, "sitepick:b")
        await site_session.handle_site_pick(ok, ctx)
        assert ctx.user_data["active_site"] == "b"
        good = make_update(111)
        assert site_session.resolve_site(good, ctx) == "b"

    async def test_ask_site_no_membership_message(self):
        ctx = make_context()
        upd = make_update("ghost")
        await site_session.ask_site(upd, ctx, resume="x")
        assert "no site membership" in \
            upd.effective_message.reply_text.call_args[0][0]


class TestSiteCommand:
    async def test_switch_validated(self):
        ctx = make_context()
        ok = make_update(111, "/site b")
        ok.message.text = "/site b"
        ctx.args = ["b"]
        await site_session.site_command(ok, ctx)
        assert ctx.user_data["active_site"] == "b"
        bad = make_update(111, "/site nope")
        ctx.args = ["nope"]
        await site_session.site_command(bad, ctx)
        assert "Not a member" in bad.effective_message.reply_text.call_args[0][0]

    async def test_list(self):
        ctx = make_context()
        upd = make_update(111, "/site")
        ctx.args = []
        await site_session.site_command(upd, ctx)
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "Your sites: a, b" in text
