"""
Integration tests for bot audit flows 13-16.

Covers:
  Flow 13: Contractor Reports (B3 handler from mventor-ticket-031)
  Flow 14: Admin User Management (/approve, /reject, /promote, /demote)
  Flow 15: /users Command
  Flow 16: /notify Command

Uses the same mocking patterns as test_bot_integration.py.
"""

import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from telegram import Update, Message, User, Chat, CallbackQuery
from telegram.ext import ContextTypes

from tests.test_bot_integration import MockHelpers


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Flow 13: Contractor Reports (B3 handler from mventor-ticket-031)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestFlow13_ContractorReports:
    """Flow 13: Contractor Reports — B3 handler from mventor-ticket-031."""

    @pytest.mark.asyncio
    async def test_13a_contractor_reports_callback(self):
        """User clicks 'Contractor Reports' -> state = 'awaiting_report_period'."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.bot.keyboards import contractor_reports_keyboard

        update = MockHelpers.mock_update(callback_data="contractor_reports")
        context = MockHelpers.mock_context(
            user_data={},
            user_role="normal_user",
            bot_data={"report_repository": MagicMock()},
        )

        await handle_main_menu_callback(update, context)

        assert context.user_data["state"] == "awaiting_report_period"
        update.callback_query.edit_message_text.assert_called_once()
        call_kwargs = update.callback_query.edit_message_text.call_args[1]
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_13b_period_selection(self):
        """User picks a period -> state = awaiting_contractor_name_for_report."""
        from app.bot.handlers.start import handle_contractor_report_period

        update = MockHelpers.mock_update(callback_data="report_period:this_week")
        context = MockHelpers.mock_context(user_data={}, user_role="normal_user")

        await handle_contractor_report_period(update, context)

        assert context.user_data["state"] == "awaiting_contractor_name_for_report"
        assert context.user_data["contractor_report_period"] == "this_week"
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Contractor Report" in call_text

    @pytest.mark.asyncio
    async def test_13c_contractor_name_matching(self):
        """Contractor name with matching entries generates consolidated PDF."""
        from app.bot.handlers.start import handle_contractor_report_name
        from app.models.database import Report, ReportItem, ReportStatus

        fixed_today = date(2026, 7, 13)

        with (
            patch("app.libre.contractor_report.ContractorReportFiller") as mock_filler_cls,
            patch("app.libre.pdf.PDFGenerator") as mock_pdf_cls,
            patch("app.bot.handlers.start.date") as mock_date,
        ):
            mock_date.today.return_value = fixed_today
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

            # Mock the filler instance
            mock_filler = MagicMock()
            mock_filler_cls.return_value = mock_filler

            # Mock the PDF generator
            mock_pdf_gen = MagicMock()
            mock_pdf_cls.return_value = mock_pdf_gen

            # Set up a mock config
            mock_config = MagicMock()
            mock_config.contractor_report_template_path = Path("templates/contractor_report_template.ots")
            mock_config.docs_folder_path = Path("exports/docs")
            mock_config.pdf_folder_path = Path("exports/pdf")

            update = MockHelpers.mock_update(message_text="Test Co")
            context = MockHelpers.mock_context(
                user_data={
                    "contractor_report_period": "this_week",
                    "state": "awaiting_contractor_name_for_report",
                },
                user_role="normal_user",
            )

            report = Report(
                date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
                items=[ReportItem(contractor="Test Co", workers=10)],
            )
            repo_mock = MagicMock()
            repo_mock.get_by_date.return_value = report
            context.bot_data["report_repository"] = repo_mock
            context.bot_data["app_config"] = mock_config

            await handle_contractor_report_name(update, context)

            # Should have sent a "generating" message
            update.message.reply_text.assert_any_call(
                ANY, parse_mode="Markdown",
            )

            # Should have created the filler with the correct template path
            mock_filler_cls.assert_called_once_with(mock_config.contractor_report_template_path)

            # Should have called fill with keyword arguments
            mock_filler.fill.assert_called_once()
            _, kwargs = mock_filler.fill.call_args
            assert kwargs.get("contractor_name") == "Test Co"

            # Should have converted to PDF
            mock_pdf_cls.assert_called_once_with(mock_config)
            mock_pdf_gen.convert_to_pdf.assert_called_once()

    @pytest.mark.asyncio
    async def test_13d_no_matching_entries(self):
        """No matching entries shows 'No entries found for X in the selected period'."""
        from app.bot.handlers.start import handle_contractor_report_name

        fixed_today = date(2026, 7, 13)

        with patch("app.bot.handlers.start.date") as mock_date:
            mock_date.today.return_value = fixed_today
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

            update = MockHelpers.mock_update(message_text="Nonexistent Co")
            context = MockHelpers.mock_context(
                user_data={
                    "contractor_report_period": "this_week",
                    "state": "awaiting_contractor_name_for_report",
                },
                user_role="normal_user",
            )

            repo_mock = MagicMock()
            repo_mock.get_by_date.return_value = None
            context.bot_data["report_repository"] = repo_mock

            await handle_contractor_report_name(update, context)

            update.message.reply_text.assert_called_once()
            call_text = update.message.reply_text.call_args[0][0]
            assert "No entries found" in call_text
            assert "Nonexistent Co" in call_text

    @pytest.mark.asyncio
    async def test_13e_unknown_period(self):
        """Unknown period shows 'Unknown period: {period}'."""
        from app.bot.handlers.start import handle_contractor_report_name

        update = MockHelpers.mock_update(message_text="Test Co")
        context = MockHelpers.mock_context(
            user_data={
                "contractor_report_period": "invalid_period",
                "state": "awaiting_contractor_name_for_report",
            },
            user_role="normal_user",
        )

        await handle_contractor_report_name(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Unknown period" in call_text
        assert "invalid_period" in call_text

    @pytest.mark.asyncio
    async def test_13f_empty_contractor_name(self):
        """Empty contractor name shows 'Please enter a valid contractor name'."""
        from app.bot.handlers.start import handle_contractor_report_name

        update = MockHelpers.mock_update(message_text="  ")
        context = MockHelpers.mock_context(
            user_data={
                "contractor_report_period": "this_week",
                "state": "awaiting_contractor_name_for_report",
            },
            user_role="normal_user",
        )

        await handle_contractor_report_name(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "valid contractor name" in call_text


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Flow 14: Admin User Management
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestFlow14_AdminUserManagement:
    """Flow 14: Admin user management commands and callbacks."""

    @pytest.mark.asyncio
    async def test_14a_approve_command(self):
        """Admin sends /approve <chat_id> -> auth.approve_user() called."""
        from app.bot.handlers.admin_users import approve_command
        from app.models.database import User as DBUser

        update = MockHelpers.mock_update(user_id=99999, message_text="/approve 12345")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_user.return_value = DBUser(chat_id="12345", role="pending")
        context.args = ["12345"]

        await approve_command(update, context)

        auth.approve_user.assert_called_once_with("12345", "99999")
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_14b_reject_command(self):
        """Admin sends /reject <chat_id> -> auth.reject_user() called."""
        from app.bot.handlers.admin_users import reject_command
        from app.models.database import User as DBUser

        update = MockHelpers.mock_update(user_id=99999, message_text="/reject 12345")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_user.return_value = DBUser(chat_id="12345", role="pending")
        context.args = ["12345"]

        await reject_command(update, context)

        auth.reject_user.assert_called_once_with("12345", "99999")

    @pytest.mark.asyncio
    async def test_14c_promote_command(self):
        """Superadmin sends /promote <chat_id> -> auth.promote_to_admin() called."""
        from app.bot.handlers.admin_users import promote_command
        from app.models.database import User as DBUser

        update = MockHelpers.mock_update(user_id=99999, message_text="/promote 12345")
        context = MockHelpers.mock_context(user_role="superadmin")
        auth = context.bot_data["authorization_service"]
        auth.get_user.return_value = DBUser(chat_id="12345", role="normal_user")
        context.args = ["12345"]

        await promote_command(update, context)

        auth.promote_to_admin.assert_called_once_with("12345", "99999")

    @pytest.mark.asyncio
    async def test_14d_demote_command(self):
        """Superadmin sends /demote <chat_id> -> auth.demote_to_user() called."""
        from app.bot.handlers.admin_users import demote_command
        from app.models.database import User as DBUser

        update = MockHelpers.mock_update(user_id=99999, message_text="/demote 12345")
        context = MockHelpers.mock_context(user_role="superadmin")
        auth = context.bot_data["authorization_service"]
        auth.get_user.return_value = DBUser(chat_id="12345", role="admin")
        context.args = ["12345"]

        await demote_command(update, context)

        auth.demote_to_user.assert_called_once_with("12345", "99999")

    @pytest.mark.asyncio
    async def test_14e_admin_cannot_promote(self):
        """Admin tries /promote -> can_promote_demote=False -> 'Unauthorized'."""
        from app.bot.handlers.admin_users import promote_command

        update = MockHelpers.mock_update(user_id=99999, message_text="/promote 12345")
        context = MockHelpers.mock_context(user_role="admin")
        context.args = ["12345"]

        await promote_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Unauthorized" in call_text

    @pytest.mark.asyncio
    async def test_14f_approve_user_callback(self):
        """Click approve_user:chat_id -> handle_approve_user_callback calls auth.approve_user()."""
        from app.bot.handlers.admin_users import handle_approve_user_callback
        from app.models.database import User as DBUser

        update = MockHelpers.mock_update(user_id=99999, callback_data="approve_user:12345")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_user.return_value = DBUser(chat_id="12345", role="pending")
        auth.get_pending_users.return_value = []

        await handle_approve_user_callback(update, context)

        auth.approve_user.assert_called_once_with("12345", "99999")

    @pytest.mark.asyncio
    async def test_14g_reject_user_callback(self):
        """Click reject_user:chat_id -> handle_reject_user_callback calls auth.reject_user()."""
        from app.bot.handlers.admin_users import handle_reject_user_callback
        from app.models.database import User as DBUser

        update = MockHelpers.mock_update(user_id=99999, callback_data="reject_user:12345")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_user.return_value = DBUser(chat_id="12345", role="pending")
        auth.get_pending_users.return_value = []

        await handle_reject_user_callback(update, context)

        auth.reject_user.assert_called_once_with("12345", "99999")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Flow 15: /users Command
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestFlow15_UsersCommand:
    """Flow 15: /users command."""

    @pytest.mark.asyncio
    async def test_15a_users_command_shows_list(self):
        """Admin sends /users -> auth.get_all_users() called -> user list shown."""
        from app.bot.handlers.admin_users import users_command
        from app.models.database import User as DBUser

        update = MockHelpers.mock_update(user_id=99999, message_text="/users")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_all_users.return_value = [
            DBUser(chat_id="12345", role="normal_user", first_name="Alice"),
            DBUser(chat_id="67890", role="admin", first_name="Bob"),
        ]

        await users_command(update, context)

        auth.get_all_users.assert_called_once()
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Alice" in call_text or "12345" in call_text

    @pytest.mark.asyncio
    async def test_15b_no_users(self):
        """No users -> 'No users registered yet.'"""
        from app.bot.handlers.admin_users import users_command

        update = MockHelpers.mock_update(user_id=99999, message_text="/users")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_all_users.return_value = []

        await users_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "No users" in call_text

    @pytest.mark.asyncio
    async def test_15c_non_admin_users(self):
        """Non-admin sends /users -> 'Unauthorized'."""
        from app.bot.handlers.admin_users import users_command

        update = MockHelpers.mock_update(message_text="/users")
        context = MockHelpers.mock_context(user_role="pending")

        await users_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Unauthorized" in call_text


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Flow 16: /notify Command
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestFlow16_NotifyCommand:
    """Flow 16: /notify command."""

    @pytest.mark.asyncio
    async def test_16a_notify_with_message(self):
        """Admin sends /notify Hello everyone! -> broadcasts to approved users."""
        from app.bot.handlers.admin_users import notify_command

        update = MockHelpers.mock_update(user_id=99999, message_text="/notify Hello everyone!")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_all_approved_chat_ids.return_value = {"123", "456"}
        context.args = ["Hello", "everyone!"]
        context.bot.send_message = AsyncMock()

        await notify_command(update, context)

        assert context.bot.send_message.call_count == 2
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "2" in call_text

    @pytest.mark.asyncio
    async def test_16b_notify_no_message(self):
        """Admin sends /notify with no message -> sends default reminder."""
        from app.bot.handlers.admin_users import notify_command

        update = MockHelpers.mock_update(user_id=99999, message_text="/notify")
        context = MockHelpers.mock_context(user_role="admin")
        auth = context.bot_data["authorization_service"]
        auth.get_all_approved_chat_ids.return_value = {"123"}
        context.args = []
        context.bot.send_message = AsyncMock()

        await notify_command(update, context)

        context.bot.send_message.assert_called_once()
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "1" in call_text

    @pytest.mark.asyncio
    async def test_16c_non_admin_notify(self):
        """Non-admin sends /notify -> 'Unauthorized'."""
        from app.bot.handlers.admin_users import notify_command

        update = MockHelpers.mock_update(message_text="/notify test")
        context = MockHelpers.mock_context(user_role="pending")

        await notify_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Unauthorized" in call_text
