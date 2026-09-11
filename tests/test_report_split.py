"""Craftsman/helper split tests (ticket-022): model/repo + entry step."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import report_create as rc
from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem
from app.repositories.report_repository import ReportRepository


def make_update(text=""):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = 111
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.reply_text = AsyncMock()
    update.message = msg
    update.callback_query = None
    update.effective_message = msg
    return update


def make_context(user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {}  # no contractor_search -> zone fallback path
    context.user_data = user_data if user_data is not None else {}
    return context


class TestCraftsmenStep:
    async def test_split_stored_and_advances(self):
        data = {"state": "awaiting_craftsmen", "current_workers": 10,
                "selected_contractor": "Test Co"}
        ctx = make_context(data)
        await rc.handle_craftsmen_input(make_update("7"), ctx)
        assert data["current_craftsmen"] == 7
        assert data["current_helpers"] == 3
        assert data["state"] == "awaiting_details"  # no search -> zone skipped

    async def test_over_worker_count_reprompts(self):
        data = {"state": "awaiting_craftsmen", "current_workers": 10}
        ctx = make_context(data)
        update = make_update("12")
        await rc.handle_craftsmen_input(update, ctx)
        assert "craftsmen cannot exceed" in \
            update.message.reply_text.call_args[0][0].lower()
        assert data["state"] == "awaiting_craftsmen"
        assert "current_craftsmen" not in data

    async def test_non_digit_reprompts(self):
        data = {"state": "awaiting_craftsmen", "current_workers": 10}
        ctx = make_context(data)
        await rc.handle_craftsmen_input(make_update("abc"), ctx)
        assert data["state"] == "awaiting_craftsmen"

    async def test_skip_clears_split(self):
        data = {"state": "awaiting_craftsmen", "current_workers": 10,
                "current_craftsmen": 4, "current_helpers": 6}
        ctx = make_context(data)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.utils.business_hours.check_business_hours",
                       AsyncMock(return_value=True))
            await rc.skip_command(make_update("/skip"), ctx)
        assert data["state"] == "awaiting_details"
        assert "current_craftsmen" not in data


class TestPersistence:
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = DatabaseManager(str(Path(tmp) / "r.db"))
            repo = ReportRepository(manager)
            report = Report(date="2026-09-11", day="Thu",
                            items=[ReportItem(contractor="Test Co",
                                              workers=10, craftsmen=4,
                                              helpers=6)])
            repo.add(report)
            loaded = repo.get_by_date("2026-09-11")
            (item,) = loaded.items
            assert (item.workers, item.craftsmen, item.helpers) == (10, 4, 6)
            manager.close_all()

    def test_old_db_gets_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.db")
            manager = DatabaseManager(path)
            manager.close_all()
            conn = sqlite3.connect(path)
            conn.execute("ALTER TABLE report_items DROP COLUMN craftsmen")
            conn.execute("ALTER TABLE report_items DROP COLUMN helpers")
            conn.commit()
            conn.close()
            reopened = DatabaseManager(path)
            assert reopened.column_exists("report_items", "craftsmen")
            assert reopened.column_exists("report_items", "helpers")
            reopened.close_all()
