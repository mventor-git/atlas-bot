"""
Integration tests for Audit Flows 9-12 of the Labor-Report bot.

Tests the following flows:
  Flow 9:  Unlock Report (callback-based, admin only)
  Flow 10: Revert Last Entry (normal_user)
  Flow 11: Revert Full Report (admin)
  Flow 12: Search
"""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update
from telegram.ext import ContextTypes

from tests.test_bot_integration import MockHelpers
from app.models.database import Report, ReportItem, ReportStatus
from app.models.search import SearchResult, SearchQuery, SearchHit


# ──────────────────────────────────────────────────────────────────────
# Flow 9: Unlock Report
# ──────────────────────────────────────────────────────────────────────


class TestFlow09_UnlockReport:
    """Flow 9: Admin unlocks a locked report via callback."""

    @pytest.mark.asyncio
    async def test_09a_admin_unlocks_locked_report(self):
        """Admin clicks 'Unlock' on locked report → workflow_service.unlock_report() called → success."""
        import app.utils.business_hours
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="unlock")

        report = Report(date="2026-07-13", day="Test", status=ReportStatus.LOCKED, items=[ReportItem(contractor="Co", workers=5)])
        unlocked = Report(date="2026-07-13", day="Test", status=ReportStatus.DRAFT, items=[ReportItem(contractor="Co", workers=5)])

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        workflow_mock = MagicMock()
        workflow_mock.unlock_report.return_value = unlocked

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={
                "report_repository": repo_mock,
                "workflow_service": workflow_mock,
            },
        )

        with patch('app.utils.business_hours.check_business_hours', new=AsyncMock(return_value=True)):
            await handle_main_menu_callback(update, context)

        workflow_mock.unlock_report.assert_called_once_with(report, "12345")
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Unlocked" in call_text or "unlocked" in call_text
        kwargs = update.callback_query.edit_message_text.call_args[1]
        assert "reply_markup" in kwargs

    @pytest.mark.asyncio
    async def test_09b_non_admin_cannot_unlock(self):
        """Non-admin clicks 'Unlock' → 'Unauthorized. Only admins can unlock.'"""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="unlock")
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": MagicMock()},
        )

        with patch('app.utils.business_hours.check_business_hours', new=AsyncMock(return_value=True)):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Unauthorized" in text
        assert "admin" in text.lower()

    @pytest.mark.asyncio
    async def test_09c_admin_unlocks_unlocked_report(self):
        """Admin tries to unlock unlocked report → 'Report is not locked'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="unlock")

        report = Report(date="2026-07-13", day="Test", status=ReportStatus.DRAFT)
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        with patch('app.utils.business_hours.check_business_hours', new=AsyncMock(return_value=True)):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        assert "not locked" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_09d_no_report_for_today(self):
        """Admin clicks unlock but no report exists → 'No report found'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="unlock")

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        with patch('app.utils.business_hours.check_business_hours', new=AsyncMock(return_value=True)):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        assert "No report found" in update.callback_query.edit_message_text.call_args[0][0]


# ──────────────────────────────────────────────────────────────────────
# Flow 10: Revert Last Entry (normal_user)
# ──────────────────────────────────────────────────────────────────────


class TestFlow10_RevertLast:
    """Flow 10: Normal user reverts the last contractor entry."""

    @pytest.mark.asyncio
    async def test_10a_normal_user_reverts_last_entry(self):
        """normal_user clicks 'Revert Last' on draft with items → last item removed, repo.update() called."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="revert_last")

        item1 = ReportItem(contractor="First Co", workers=5)
        item2 = ReportItem(contractor="Last Co", workers=10)
        report = Report(date="2026-07-13", day="Test", status=ReportStatus.DRAFT, items=[item1, item2])

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await handle_main_menu_callback(update, context)

        repo_mock.update.assert_called_once()
        updated_report = repo_mock.update.call_args[0][0]
        assert len(updated_report.items) == 1
        assert updated_report.items[0].contractor == "First Co"

    @pytest.mark.asyncio
    async def test_10b_audit_logged_with_contractor_name(self):
        """Revert last → audit.log_reverted() called with contractor_name."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.services.audit_service import AuditService

        update = MockHelpers.mock_update(callback_data="revert_last")

        item = ReportItem(contractor="Target Co", workers=7)
        report = Report(date="2026-07-13", day="Test", status=ReportStatus.DRAFT, items=[item])

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        audit_mock = MagicMock(spec=AuditService)

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={
                "report_repository": repo_mock,
                "audit_service": audit_mock,
            },
        )

        await handle_main_menu_callback(update, context)

        audit_mock.log_reverted.assert_called_once()
        call_kwargs = audit_mock.log_reverted.call_args[1]
        assert call_kwargs.get("contractor_name") == "Target Co"

    @pytest.mark.asyncio
    async def test_10c_draft_has_no_items(self):
        """Draft has no items → 'Nothing to revert'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="revert_last")

        report = Report(date="2026-07-13", day="Test", status=ReportStatus.DRAFT, items=[])

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        assert "Nothing to revert" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_10d_report_is_final_or_locked(self):
        """Report is final/locked → 'Cannot revert: report is final/locked'."""
        from app.bot.handlers.start import handle_main_menu_callback

        for status in (ReportStatus.FINAL, ReportStatus.LOCKED):
            update = MockHelpers.mock_update(callback_data="revert_last")

            report = Report(date="2026-07-13", day="Test", status=status, items=[ReportItem(contractor="Co", workers=3)])

            repo_mock = MagicMock()
            repo_mock.get_by_date.return_value = report

            context = MockHelpers.mock_context(
                user_role="normal_user",
                bot_data={"report_repository": repo_mock},
            )

            await handle_main_menu_callback(update, context)

            update.callback_query.edit_message_text.assert_called_once()
            text = update.callback_query.edit_message_text.call_args[0][0]
            assert "Cannot revert" in text
            assert status.value in text


# ──────────────────────────────────────────────────────────────────────
# Flow 11: Revert Full Report (admin)
# ──────────────────────────────────────────────────────────────────────


class TestFlow11_RevertFullReport:
    """Flow 11: Admin reverts the entire report."""

    @pytest.mark.asyncio
    async def test_11a_admin_reverts_full_report(self):
        """Admin clicks 'Revert Report' on draft → repo.delete() called, audit.log_reverted() with [FULL_REPORT]."""
        from app.bot.handlers.start import handle_main_menu_callback
        from app.services.audit_service import AuditService

        update = MockHelpers.mock_update(callback_data="revert_report")

        item = ReportItem(contractor="Some Co", workers=8)
        report = Report(date="2026-07-13", day="Test", status=ReportStatus.DRAFT, items=[item], id=42)

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        audit_mock = MagicMock(spec=AuditService)

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={
                "report_repository": repo_mock,
                "audit_service": audit_mock,
            },
        )

        await handle_main_menu_callback(update, context)

        repo_mock.delete.assert_called_once_with(42)
        audit_mock.log_reverted.assert_called_once()
        call_kwargs = audit_mock.log_reverted.call_args[1]
        assert call_kwargs.get("contractor_name") == "[FULL_REPORT]"
        update.callback_query.edit_message_text.assert_called_once()
        assert "reverted" in update.callback_query.edit_message_text.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_11b_non_admin_cannot_revert_report(self):
        """Non-admin tries 'Revert Report' → 'Only admins can revert'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="revert_report")
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": MagicMock()},
        )

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Only admins" in text

    @pytest.mark.asyncio
    async def test_11c_no_report_for_today(self):
        """Admin clicks 'Revert Report' but no report → 'No report found for today'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="revert_report")

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "No report found" in text

    @pytest.mark.asyncio
    async def test_11d_report_not_draft(self):
        """Admin tries to revert non-draft report → 'Cannot revert'."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="revert_report")

        report = Report(date="2026-07-13", day="Test", status=ReportStatus.FINAL, items=[ReportItem(contractor="Co", workers=3)], id=42)
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        assert "Cannot revert" in update.callback_query.edit_message_text.call_args[0][0]


# ──────────────────────────────────────────────────────────────────────
# Flow 12: Search
# ──────────────────────────────────────────────────────────────────────


class TestFlow12_Search:
    """Flow 12: Search reports by date, contractor, or keyword."""

    @pytest.mark.asyncio
    async def test_12a_search_command_sets_state(self):
        """User clicks 'Search' → state = 'awaiting_search_query', prompt shown."""
        from app.bot.handlers.search import search_command

        update = MockHelpers.mock_update(message_text="/search")
        context = MockHelpers.mock_context(user_data={})

        await search_command(update, context)

        assert context.user_data.get("state") == "awaiting_search_query"
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_12b_search_query_with_results(self):
        """User sends search text → universal_search_service.search() called → results shown."""
        from app.bot.handlers.search import handle_search_query

        update = MockHelpers.mock_update(message_text="Test Contractor")
        update.message.text = "Test Contractor"

        hit = SearchHit(report_id=1, date="2026-01-15", day="Monday",
                        status=ReportStatus.DRAFT, contractor_count=3, total_workers=10)
        search_result = SearchResult(query=SearchQuery(text="Test Contractor"), hits=[hit], total_count=1)

        search_service_mock = MagicMock()
        search_service_mock.search.return_value = search_result

        context = MockHelpers.mock_context(
            user_data={"state": "awaiting_search_query"},
            bot_data={
                "universal_search_service": search_service_mock,
                "report_repository": MagicMock(),
            },
        )

        await handle_search_query(update, context)

        search_service_mock.search.assert_called_once()
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Search Results" in call_text

    @pytest.mark.asyncio
    async def test_12c_search_no_results(self):
        """No results found → 'No results found'."""
        from app.bot.handlers.search import handle_search_query

        update = MockHelpers.mock_update(message_text="Nonexistent")
        update.message.text = "Nonexistent"

        search_result = SearchResult(query=SearchQuery(text="Nonexistent"), hits=[], total_count=0)

        search_service_mock = MagicMock()
        search_service_mock.search.return_value = search_result

        context = MockHelpers.mock_context(
            user_data={"state": "awaiting_search_query"},
            bot_data={
                "universal_search_service": search_service_mock,
                "report_repository": MagicMock(),
            },
        )

        await handle_search_query(update, context)

        search_service_mock.search.assert_called_once()
        update.message.reply_text.assert_called_once()
        assert "No results found" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_12d_view_report_from_search_results(self):
        """User clicks result (view_report:2026-01-15) → report detail shown."""
        from app.bot.handlers.search import handle_view_report_callback

        update = MockHelpers.mock_update(callback_data="view_report:2026-01-15")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(date="2026-01-15", day="Monday", status=ReportStatus.DRAFT, items=[item])

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            bot_data={"report_repository": repo_mock},
        )

        await handle_view_report_callback(update, context)

        repo_mock.get_by_date.assert_called_once_with("2026-01-15")
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Test Co" in call_text
        assert "2026-01-15" in call_text

    @pytest.mark.asyncio
    async def test_12e_search_page_pagination(self):
        """User clicks page (search_page:1) → re-queries with pagination."""
        from app.bot.handlers.search import handle_search_page

        update = MockHelpers.mock_update(callback_data="search_page:1")

        hit = SearchHit(report_id=2, date="2026-01-20", day="Tue",
                        status=ReportStatus.FINAL, contractor_count=5, total_workers=25)
        search_result = SearchResult(query=SearchQuery(text="test", page=1, page_size=5),
                                     hits=[hit], total_count=6)

        search_service_mock = MagicMock()
        search_service_mock.search.return_value = search_result

        context = MockHelpers.mock_context(
            user_data={
                "last_search_query_text": "test",
                "last_search_total_pages": 2,
                "last_search_page_size": 5,
            },
            bot_data={
                "universal_search_service": search_service_mock,
            },
        )

        await handle_search_page(update, context)

        search_service_mock.search.assert_called_once()
        call_args = search_service_mock.search.call_args[0][0]
        assert call_args.page == 1
        assert call_args.page_size == 5
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Search Results" in call_text
