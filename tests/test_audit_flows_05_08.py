"""
Integration tests for Audit Flows 5-8.

Tests the following flows:
- Flow 5: View Report
- Flow 6: Download / Preview PDF
- Flow 7: Finalize Report
- Flow 8: Lock Report
"""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from types import SimpleNamespace

from telegram import Update
from telegram.ext import ContextTypes


class MockHelpers:
    """Helper methods for creating mock Telegram objects."""

    @staticmethod
    def mock_callback_query(data: str = ""):
        from telegram import CallbackQuery
        cq = MagicMock(spec=CallbackQuery)
        cq.data = data
        cq.answer = AsyncMock()
        cq.edit_message_text = AsyncMock()
        cq.edit_message_reply_markup = AsyncMock()
        cq.message = MagicMock()
        cq.message.reply_text = AsyncMock()
        cq.message.edit_message_text = AsyncMock()
        return cq

    @staticmethod
    def mock_message(text: str = ""):
        from telegram import Message
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.reply_text = AsyncMock()
        msg.reply_html = AsyncMock()
        return msg

    @staticmethod
    def mock_update(user_id: int = 12345, message_text: str = "", callback_data: str = None) -> Update:
        update = MagicMock(spec=Update)
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = user_id

        if callback_data is not None:
            update.callback_query = MockHelpers.mock_callback_query(callback_data)
            update.callback_query.from_user = MagicMock()
            update.callback_query.from_user.id = user_id
            update.message = None
        else:
            update.message = MockHelpers.mock_message(message_text)
            update.message.from_user = MagicMock()
            update.message.from_user.id = user_id
            update.callback_query = None

        return update

    @staticmethod
    def mock_authorization_service(role: str = "normal_user") -> MagicMock:
        from app.services.authorization_service import AuthorizationService
        mock = MagicMock(spec=AuthorizationService)
        mock.get_role.return_value = role
        mock.register_or_get.return_value = MagicMock()
        mock.is_admin.return_value = role in ("superadmin", "admin")
        mock.is_super_admin.return_value = role == "superadmin"
        mock.is_authorized.return_value = role not in ("pending", "rejected")
        mock.can_view_reports.return_value = role not in ("pending", "rejected")
        mock.can_create_reports.return_value = role in ("superadmin", "admin", "normal_user")
        mock.can_finalize.return_value = role in ("superadmin", "admin")
        mock.can_manage_users.return_value = role in ("superadmin", "admin")
        mock.get_super_admin_chat_id.return_value = "999999999"
        mock.get_all_admin_chat_ids.return_value = {"999999999", "888888888"}
        return mock

    @staticmethod
    def mock_context(bot_data: dict = None, user_data: dict = None, user_role: str = None) -> ContextTypes.DEFAULT_TYPE:
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        base_bot_data = {}
        if user_role is not None:
            base_bot_data["authorization_service"] = MockHelpers.mock_authorization_service(user_role)
        base_bot_data.update(bot_data or {})
        context.bot_data = base_bot_data
        context.user_data = user_data or {}
        context.bot = MagicMock()
        context.bot.send_document = AsyncMock()
        context.bot.send_message = AsyncMock()
        return context


# =========================================================================
# Flow 5: View Report
# =========================================================================


class TestFlow5_ViewReport:
    """Test the View Report flow."""

    @pytest.mark.asyncio
    async def test_5a_normal_user_views_report_with_items(self):
        """normal_user clicks 'View Report' -> report text shown, audit.log_viewed() called."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="view_report")

        item = ReportItem(contractor="Test Co", workers=10, zone="Zone1", details="Some work")
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
            finalized_at="2026-07-13T10:00:00",
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        audit_mock = MagicMock()

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={
                "report_repository": repo_mock,
                "audit_service": audit_mock,
            },
        )

        await handle_main_menu_callback(update, context)

        repo_mock.get_by_date.assert_called_once_with(date.today().isoformat())
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Test Co" in call_text
        assert "10" in call_text
        assert "Zone1" in call_text
        assert "final" in call_text.lower()

        audit_mock.log_viewed.assert_called_once()

    @pytest.mark.asyncio
    async def test_5b_no_report_today(self):
        """No report today -> 'No report found' message."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="view_report")

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await handle_main_menu_callback(update, context)

        repo_mock.get_by_date.assert_called_once()
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "No report found" in call_text

    @pytest.mark.asyncio
    async def test_5c_pending_user_no_permission(self):
        """pending user -> auth.can_view_reports() = False -> 'You don't have permission'."""
        from app.bot.handlers.start import view_command

        update = MockHelpers.mock_update(message_text="/view")

        auth = MockHelpers.mock_authorization_service("pending")
        auth.can_view_reports.return_value = False

        context = MockHelpers.mock_context(
            bot_data={"authorization_service": auth},
        )

        await view_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "don't have permission" in call_text.lower()


# =========================================================================
# Flow 6: Download / Preview PDF
# =========================================================================


class TestFlow6_DownloadPreviewPDF:
    """Test the Download / Preview PDF flow."""

    @pytest.mark.asyncio
    async def test_6a_download_pdf_generates_and_sends(self):
        """normal_user clicks 'Download PDF' -> generate_preview() called -> send_document() called."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="download_pdf")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        pdf_mock = MagicMock()
        pdf_mock.generate_preview.return_value = "/tmp/preview_Labor_Report_2026-07-13.pdf"

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={
                "report_repository": repo_mock,
                "pdf_preview_service": pdf_mock,
            },
        )

        with patch("pathlib.Path.read_bytes", return_value=b"fake_pdf_bytes"):
            with patch("app.bot.handlers.start.InputFile"):
                await handle_main_menu_callback(update, context)

        pdf_mock.generate_preview.assert_called_once_with(report)
        context.bot.send_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_6b_audit_log_exported_on_pdf_generation(self):
        """audit.log_exported() called when PDF generated."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="download_pdf")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        pdf_mock = MagicMock()
        pdf_mock.generate_preview.return_value = "/tmp/preview.pdf"

        audit_mock = MagicMock()

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={
                "report_repository": repo_mock,
                "pdf_preview_service": pdf_mock,
                "audit_service": audit_mock,
            },
        )

        with patch("pathlib.Path.read_bytes", return_value=b"fake_pdf_bytes"):
            with patch("app.bot.handlers.start.InputFile"):
                await handle_main_menu_callback(update, context)

        audit_mock.log_exported.assert_called_once()

    @pytest.mark.asyncio
    async def test_6c_no_report_items_shows_message(self):
        """No report items -> 'No report data available' message."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(callback_data="download_pdf")

        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            items=[], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "No report data available" in call_text

    @pytest.mark.asyncio
    async def test_6d_no_pdf_service_shows_excel_message(self):
        """No pdf_preview_service in bot_data -> 'PDF generation requires Excel' message."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="download_pdf")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Excel" in call_text


# =========================================================================
# Flow 7: Finalize Report
# =========================================================================


class TestFlow7_FinalizeReport:
    """Test the Finalize Report flow."""

    @pytest.mark.asyncio
    async def test_7a_admin_finalize_shows_confirmation(self):
        """admin clicks 'Finalize' on draft with items -> confirmation shown."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="finalize")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        assert context.user_data.get("state") == "awaiting_finalize_confirmation"
        assert context.user_data.get("current_report") is report
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Finalize" in call_text
        assert "Are you sure" in call_text
        call_kwargs = update.callback_query.edit_message_text.call_args[1]
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_7b_normal_user_no_permission(self):
        """normal_user clicks 'Finalize' -> 'You don't have permission to finalize reports'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="finalize")

        auth = MockHelpers.mock_authorization_service("normal_user")
        auth.can_finalize.return_value = False

        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "report_repository": MagicMock(),
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "don't have permission" in call_text.lower()
        assert "finalize" in call_text.lower()

    @pytest.mark.asyncio
    async def test_7c_confirm_finalize_calls_service(self):
        """User confirms (confirm:finalize) -> workflow_service.finalize_report() called -> success message."""
        from app.bot.handlers.start import handle_confirmation_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="confirm:finalize")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            items=[item], id=1,
        )
        finalized_report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
            finalized_at="2026-07-13T10:00:00",
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        workflow_mock = MagicMock()
        workflow_mock.finalize_report.return_value = finalized_report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={
                "report_repository": repo_mock,
                "workflow_service": workflow_mock,
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_confirmation_callback(update, context)

        workflow_mock.finalize_report.assert_called_once_with(report, "12345")
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "finalized" in call_text.lower() or "successfully" in call_text.lower()

    @pytest.mark.asyncio
    async def test_7d_empty_report_cannot_finalize(self):
        """Empty report -> 'Cannot finalize an empty report'."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(callback_data="finalize")

        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            items=[], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Cannot finalize an empty report" in call_text

    @pytest.mark.asyncio
    async def test_7e_already_final_cannot_finalize_again(self):
        """Report already final -> 'Cannot finalize again'."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="finalize")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Cannot finalize again" in call_text

    @pytest.mark.asyncio
    async def test_7f_cancel_finalize_clears_state(self):
        """User cancels (cancel:finalize) -> state cleared, dashboard shown."""
        from app.bot.handlers.start import handle_confirmation_callback

        update = MockHelpers.mock_update(callback_data="cancel:finalize")
        context = MockHelpers.mock_context(
            user_role="admin",
            user_data={"state": "awaiting_finalize_confirmation", "current_report": "dummy"},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            with patch("app.bot.handlers.start.dashboard_callback", new=AsyncMock()):
                await handle_confirmation_callback(update, context)

        assert len(context.user_data) == 0


# =========================================================================
# Flow 8: Lock Report
# =========================================================================


class TestFlow8_LockReport:
    """Test the Lock Report flow."""

    @pytest.mark.asyncio
    async def test_8a_admin_lock_on_final_shows_confirmation(self):
        """admin clicks 'Lock' on final report -> confirmation shown."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="lock")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        assert context.user_data.get("state") == "awaiting_lock_confirmation"
        assert context.user_data.get("current_report") is report
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Lock" in call_text
        assert "Are you sure" in call_text
        call_kwargs = update.callback_query.edit_message_text.call_args[1]
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_8b_normal_user_no_permission(self):
        """normal_user clicks 'Lock' -> 'You don't have permission to lock reports'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="lock")

        auth = MockHelpers.mock_authorization_service("normal_user")
        auth.can_finalize.return_value = False

        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "report_repository": MagicMock(),
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "don't have permission" in call_text.lower()
        assert "lock" in call_text.lower()

    @pytest.mark.asyncio
    async def test_8c_confirm_lock_calls_service(self):
        """User confirms (confirm:lock) -> workflow_service.lock_report() called -> success."""
        from app.bot.handlers.start import handle_confirmation_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="confirm:lock")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.FINAL,
            items=[item], id=1,
        )
        locked_report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.LOCKED,
            items=[item], id=1,
            locked_at="2026-07-13T10:00:00",
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        workflow_mock = MagicMock()
        workflow_mock.lock_report.return_value = locked_report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={
                "report_repository": repo_mock,
                "workflow_service": workflow_mock,
            },
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_confirmation_callback(update, context)

        workflow_mock.lock_report.assert_called_once_with(report, "12345")
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "locked" in call_text.lower() or "successfully" in call_text.lower()

    @pytest.mark.asyncio
    async def test_8d_report_not_final_shows_message(self):
        """Report not in final state -> appropriate message."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(callback_data="lock")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "must be" in call_text.lower() and "final" in call_text.lower()
