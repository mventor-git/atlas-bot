"""
Integration tests for bot audit flows 17–20.

Flow 17: /get <date> + Date Text Parsing
Flow 18: Fresh Start (/fresh)
Flow 19: Cancel / Back / Skip Navigation
Flow 20: Authorization Edge Cases
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from telegram import Update
from telegram.ext import ContextTypes

from tests.test_bot_integration import MockHelpers


# ═══════════════════════════════════════════════════════════════════
# Flow 17: /get <date> + Date Text Parsing
# ═══════════════════════════════════════════════════════════════════


class TestFlow17GetCommandAndDateParsing:
    """Flow 17: /get <date> + Date Text Parsing."""

    # -- 17a: /get 2026-07-12 (YYYY-MM-DD) -------------------------

    @pytest.mark.asyncio
    async def test_17a_get_command_yyyy_mm_dd(self):
        """User sends /get 2026-07-12 -> parse_date_string parses YYYY-MM-DD ->
        report found -> PDF sent via reply_document."""
        from app.bot.handlers.report_retrieval import get_report_command
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(message_text="/get 2026-07-12")
        update.message.reply_document = AsyncMock()

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-12",
            day="Monday",
            status=ReportStatus.FINAL,
            items=[item],
            preview_pdf_path="test.pdf",
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )
        context.args = ["2026-07-12"]

        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=b"PDF")):
                await get_report_command(update, context)

        update.message.reply_document.assert_called_once()
        filename = update.message.reply_document.call_args[1].get("filename", "")
        assert "labor_report_2026-07-12" in filename

    # -- 17b: Date text DD-MM-YYYY (no active state) ---------------

    @pytest.mark.asyncio
    async def test_17b_date_text_dd_mm_yyyy(self):
        """User sends 12-07-2026 (no active state) -> handle_date_text()
        parses DD-MM-YYYY -> report found -> PDF sent."""
        from app.bot.handlers.report_create import handle_text_message
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(message_text="12-07-2026")
        update.message.reply_document = AsyncMock()

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-12",
            day="Monday",
            status=ReportStatus.FINAL,
            items=[item],
            preview_pdf_path="test.pdf",
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
            user_data={},
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=b"PDF")):
                await handle_text_message(update, context)

        update.message.reply_document.assert_called_once()

    # -- 17c: Date text "yesterday" (no active state) --------------

    @pytest.mark.asyncio
    async def test_17c_date_text_yesterday(self):
        """User sends yesterday (no active state) -> resolves to
        yesterday's date -> PDF sent (or no-report fallback)."""
        from app.bot.handlers.report_create import handle_text_message
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(message_text="yesterday")
        update.message.reply_document = AsyncMock()

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-11",
            day="Sunday",
            status=ReportStatus.FINAL,
            items=[item],
            preview_pdf_path="test.pdf",
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
            user_data={},
        )

        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=b"PDF")):
                await handle_text_message(update, context)

        # Either PDF was sent, or "No report found" for that date
        if update.message.reply_document.called:
            pass  # PDF sent — success
        else:
            update.message.reply_text.assert_called_once()
            assert "No report found" in update.message.reply_text.call_args[0][0]

    # -- 17d: No report for that date ------------------------------

    @pytest.mark.asyncio
    async def test_17d_no_report_found(self):
        """No report for that date -> 'No report found for {date}'."""
        from app.bot.handlers.report_retrieval import get_report_command

        update = MockHelpers.mock_update(message_text="/get 2026-07-12")

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )
        context.args = ["2026-07-12"]

        await get_report_command(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "No Report Found" in text
        assert "2026-07-12" in text

    # -- 17e: Invalid date falls to default ------------------------

    @pytest.mark.asyncio
    async def test_17e_invalid_date_falls_to_default(self):
        """Invalid date text -> falls through to default handler
        -> 'I'm not sure what to do with that'."""
        from app.bot.handlers.report_create import handle_text_message

        update = MockHelpers.mock_update(message_text="not a date at all")
        context = MockHelpers.mock_context(user_data={"state": "some_unknown_state"})

        await handle_text_message(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "I'm not sure what to do with that" in text


# ═══════════════════════════════════════════════════════════════════
# Flow 18: Fresh Start (/fresh)
# ═══════════════════════════════════════════════════════════════════


class TestFlow18FreshStart:
    """Flow 18: Fresh Start (/fresh)."""

    @pytest.mark.asyncio
    async def test_18a_fresh_start_existing_draft(self):
        """normal_user sends /fresh with existing unlocked draft ->
        repo.delete() called -> 'Fresh Start'."""
        from app.bot.handlers.start import fresh_start_command
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(message_text="/fresh")

        report = Report(date="2026-07-12", day="Test", status=ReportStatus.DRAFT, id=1)
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report
        repo_mock.delete = MagicMock(return_value=True)

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await fresh_start_command(update, context)

        repo_mock.delete.assert_called_once_with(report.id)
        update.message.reply_text.assert_called_once()
        assert "Fresh Start" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_18b_fresh_start_locked_report(self):
        """normal_user sends /fresh with locked report ->
        'Cannot delete locked report'."""
        from app.bot.handlers.start import fresh_start_command
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(message_text="/fresh")

        report = Report(date="2026-07-12", day="Test", status=ReportStatus.LOCKED)
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await fresh_start_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "locked" in update.message.reply_text.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_18c_fresh_start_no_report(self):
        """normal_user sends /fresh with no report ->
        'No report found, you're already fresh'."""
        from app.bot.handlers.start import fresh_start_command

        update = MockHelpers.mock_update(message_text="/fresh")

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await fresh_start_command(update, context)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "No report found" in text
        assert "already fresh" in text.lower()


# ═══════════════════════════════════════════════════════════════════
# Flow 19: Cancel / Back / Skip Navigation
# ═══════════════════════════════════════════════════════════════════


class TestFlow19CancelBackSkipNavigation:
    """Flow 19: Cancel / Back / Skip Navigation."""

    @pytest.mark.asyncio
    async def test_19a_cancel_command(self):
        """/cancel -> user_data cleared, dashboard shown."""
        from app.bot.handlers.start import cancel_command

        update = MockHelpers.mock_update(message_text="/cancel")
        context = MockHelpers.mock_context(user_data={"state": "test", "some_key": "value"})

        with patch("app.bot.handlers.start.start_command", new=AsyncMock()):
            await cancel_command(update, context)

        assert len(context.user_data) == 0

    @pytest.mark.asyncio
    async def test_19b_back_from_awaiting_details(self):
        """/back from awaiting_details -> state changes to awaiting_zone."""
        from app.bot.handlers.report_create import back_command

        update = MockHelpers.mock_update(message_text="/back")
        context = MockHelpers.mock_context(
            user_data={
                "state": "awaiting_details",
                "selected_contractor": "Test Co",
                "current_workers": 10,
            },
        )

        contractor_search = MagicMock()
        contractor_search.get_all_zones.return_value = []
        context.bot_data["contractor_search"] = contractor_search

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await back_command(update, context)

        assert context.user_data["state"] == "awaiting_zone"

    @pytest.mark.asyncio
    async def test_19c_back_from_awaiting_zone(self):
        """/back from awaiting_zone -> state changes to awaiting_worker_count."""
        from app.bot.handlers.report_create import back_command

        update = MockHelpers.mock_update(message_text="/back")
        context = MockHelpers.mock_context(
            user_data={
                "state": "awaiting_zone",
                "selected_contractor": "Test Co",
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await back_command(update, context)

        assert context.user_data["state"] == "awaiting_worker_count"

    @pytest.mark.asyncio
    async def test_19d_skip_on_details(self):
        """/skip on details -> handle_details_input called (details set to None)."""
        from app.bot.handlers.report_create import skip_command
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(message_text="/skip")

        report = Report(date="2026-07-12", day="Test", status=ReportStatus.DRAFT, items=[])

        auto_save_mock = MagicMock()
        auto_save_mock.auto_save_item.return_value = report

        contractor_search = MagicMock()
        contractor_search.get_all_contractors.return_value = []

        context = MockHelpers.mock_context(
            user_data={
                "state": "awaiting_details",
                "selected_contractor": "Test Co",
                "selected_contractor_raw_name": "Test Co",
                "selected_contractor_type": None,
                "selected_contractor_code": "Test Co",
                "current_workers": 10,
                "current_zone": "Zone A",
                "current_report": report,
            },
            bot_data={
                "auto_save_service": auto_save_mock,
                "contractor_search": contractor_search,
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await skip_command(update, context)

        auto_save_mock.auto_save_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_19e_skip_on_zone(self):
        """/skip on zone -> sets zone=None, goes to awaiting_details."""
        from app.bot.handlers.report_create import skip_command

        update = MockHelpers.mock_update(message_text="/skip")
        context = MockHelpers.mock_context(
            user_data={
                "state": "awaiting_zone",
                "selected_contractor": "Test Co",
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await skip_command(update, context)

        assert context.user_data["current_zone"] is None
        assert context.user_data["state"] == "awaiting_details"

    @pytest.mark.asyncio
    async def test_19f_cancel_report_callback(self):
        """Click 'Cancel' on contractor_selection_keyboard (cancel_report) ->
        user_data cleared, query.answer() called (B2 fix verified)."""
        from app.bot.handlers.report_create import cancel_report

        update = MockHelpers.mock_update(callback_data="cancel_report")
        context = MockHelpers.mock_context(user_data={"state": "test", "some_key": "value"})

        with patch("app.bot.handlers.start.dashboard_callback", new=AsyncMock()):
            await cancel_report(update, context)

        update.callback_query.answer.assert_called_once()
        assert len(context.user_data) == 0


# ═══════════════════════════════════════════════════════════════════
# Flow 20: Authorization Edge Cases
# ═══════════════════════════════════════════════════════════════════


class TestFlow20AuthorizationEdgeCases:
    """Flow 20: Authorization Edge Cases."""

    @pytest.mark.asyncio
    async def test_20a_pending_cannot_view_report(self):
        """Pending user tries view_report -> can_view_reports() = False
        -> 'You don't have permission'."""
        from app.bot.handlers.start import view_command

        update = MockHelpers.mock_update(message_text="/view")
        context = MockHelpers.mock_context(user_role="pending")

        await view_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "don't have permission" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_20b_viewer_cannot_create_report(self):
        """Viewer tries create_report -> can_create_reports() = False
        -> 'You don't have permission'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="create_report")
        context = MockHelpers.mock_context(user_role="viewer", user_data={})

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        assert "don't have permission" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_20c_admin_can_create_report(self):
        """Admin can create reports -> can_create_reports() = True
        -> flow proceeds (no permission error)."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="create_report")

        auto_save_mock = MagicMock()
        auto_save_mock.save_draft.return_value = MagicMock(id=1)

        contractor_search = MagicMock()
        contractor_search.get_all_contractors.return_value = []

        context = MockHelpers.mock_context(
            user_role="admin",
            user_data={},
            bot_data={
                "auto_save_service": auto_save_mock,
                "contractor_search": contractor_search,
                "report_repository": MagicMock(),
                "daily_dashboard_service": MagicMock(),
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "permission" not in text.lower()
        assert "Create Report" in text

    @pytest.mark.asyncio
    async def test_20d_normal_user_cannot_finalize(self):
        """Normal user tries to finalize -> can_finalize() = False
        -> 'You don't have permission'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="finalize")
        context = MockHelpers.mock_context(
            user_role="normal_user",
            user_data={},
            bot_data={"report_repository": MagicMock()},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        assert "don't have permission" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_20e_no_authorization_service(self):
        """No authorization_service in bot_data ->
        guard shows 'Authorization not available'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="finalize")
        context = MockHelpers.mock_context()  # no user_role -> no auth service

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        assert "Authorization not available" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_20f_outside_business_hours(self):
        """Outside business hours -> edit operation blocked
        -> 'After Hours' message."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="finalize")
        context = MockHelpers.mock_context(user_role="admin", user_data={})

        with patch("app.utils.business_hours.is_business_hours", return_value=False):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "After Hours" in text or "read-only" in text
