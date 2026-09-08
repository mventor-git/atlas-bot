"""
Integration tests for Audit Flows 1-4.

Tests the following flows:
- Flow 1: User Registration & Access (/start)
- Flow 2: Create Report (Button Path)
- Flow 3: Create Report (/new Command)
- Flow 4: Copy Yesterday
"""

import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from types import SimpleNamespace

from telegram import Update, User, Chat, Message, CallbackQuery
from telegram.ext import ContextTypes


class MockHelpers:
    """Helper methods for creating mock Telegram objects."""

    @staticmethod
    def mock_message(text: str = "") -> Message:
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.reply_text = AsyncMock()
        msg.reply_html = AsyncMock()
        return msg

    @staticmethod
    def mock_callback_query(data: str = "") -> CallbackQuery:
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
    def mock_update(user_id: int = 12345, message_text: str = "", callback_data: str = None) -> Update:
        update = MagicMock(spec=Update)
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_user.username = f"user{user_id}"
        update.effective_user.first_name = f"User{user_id}"
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
# Flow 1: User Registration & Access (/start)
# =========================================================================


class TestFlow1_UserRegistrationAndAccess:
    """Test /start command behavior for different user roles."""

    @pytest.fixture
    def mock_dashboard(self):
        dash = MagicMock()
        dash.get_dashboard.return_value = SimpleNamespace(
            date="2026-07-13",
            day="Monday",
            time="10:00 AM",
            report_status="not_created",
            contractor_count=0,
            total_workers=0,
            time_remaining="4h",
        )
        return dash

    @pytest.fixture
    def mock_repo(self):
        repo = MagicMock()
        repo.get_all.return_value = []
        return repo

    @pytest.mark.asyncio
    async def test_1a_new_user_pending(self, mock_dashboard, mock_repo):
        """New user (pending role) sends /start -> register_or_get called, pending message shown."""
        from app.bot.handlers.start import start_command

        update = MockHelpers.mock_update(message_text="/start")
        context = MockHelpers.mock_context(
            user_role="pending",
            bot_data={
                "daily_dashboard_service": mock_dashboard,
                "report_repository": mock_repo,
            },
        )

        with patch("app.bot.handlers.start._notify_admin_new_user"):
            await start_command(update, context)

        auth = context.bot_data["authorization_service"]
        auth.register_or_get.assert_called_once()

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Pending" in call_text or "pending approval" in call_text

    @pytest.mark.asyncio
    async def test_1b_pending_user_sees_pending_message(self, mock_dashboard, mock_repo):
        """Pending user sees 'pending approval' message and not dashboard."""
        from app.bot.handlers.start import start_command

        update = MockHelpers.mock_update(message_text="/start")
        auth = MockHelpers.mock_authorization_service("pending")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "daily_dashboard_service": mock_dashboard,
                "report_repository": mock_repo,
            },
        )

        with patch("app.bot.handlers.start._notify_admin_new_user"):
            await start_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Access Pending" in call_text or "pending approval" in call_text

        mock_dashboard.get_dashboard.assert_not_called()

    @pytest.mark.asyncio
    async def test_1c_approved_user_sees_dashboard(self, mock_dashboard, mock_repo):
        """Approved normal_user sees dashboard with buttons."""
        from app.bot.handlers.start import start_command

        update = MockHelpers.mock_update(message_text="/start")
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={
                "daily_dashboard_service": mock_dashboard,
                "report_repository": mock_repo,
            },
        )

        await start_command(update, context)

        mock_dashboard.get_dashboard.assert_called_once()

        update.message.reply_text.assert_called_once()
        call_kwargs = update.message.reply_text.call_args[1]
        assert "reply_markup" in call_kwargs
        assert call_kwargs.get("parse_mode") == "Markdown"

    @pytest.mark.asyncio
    async def test_1d_rejected_user_sees_denied(self, mock_dashboard, mock_repo):
        """Rejected user sees 'access denied' message."""
        from app.bot.handlers.start import start_command

        update = MockHelpers.mock_update(message_text="/start")
        auth = MockHelpers.mock_authorization_service("rejected")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "daily_dashboard_service": mock_dashboard,
                "report_repository": mock_repo,
            },
        )

        await start_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Access Denied" in call_text or "denied" in call_text.lower()

        mock_dashboard.get_dashboard.assert_not_called()

    @pytest.mark.asyncio
    async def test_1e_viewer_sees_dashboard(self, mock_dashboard, mock_repo):
        """Viewer user sees dashboard."""
        from app.bot.handlers.start import start_command

        update = MockHelpers.mock_update(message_text="/start")
        auth = MockHelpers.mock_authorization_service("viewer")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "daily_dashboard_service": mock_dashboard,
                "report_repository": mock_repo,
            },
        )

        await start_command(update, context)

        mock_dashboard.get_dashboard.assert_called_once()

        update.message.reply_text.assert_called_once()
        call_kwargs = update.message.reply_text.call_args[1]
        assert "reply_markup" in call_kwargs


# =========================================================================
# Flow 2: Create Report (Button Path)
# =========================================================================


class TestFlow2_CreateReportButton:
    """Test creating a report via the 'Create Report' button."""

    @pytest.fixture
    def mock_contractor_search(self):
        cs = MagicMock()
        from app.models.database import Contractor
        cs.get_all_contractors.return_value = [
            Contractor(name="Test Co", type="Civil"),
            Contractor(name="Another Co", type="Electrical"),
        ]
        cs.get_all_zones.return_value = []
        return cs

    @pytest.fixture
    def mock_auto_save(self):
        asv = MagicMock()
        from app.models.database import Report, ReportStatus
        asv.save_draft.return_value = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            telegram_user="12345", id=1,
        )
        asv.auto_save_item.return_value = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            telegram_user="12345", id=1, items=[],
        )
        return asv

    @pytest.fixture
    def mock_audit(self):
        audit = MagicMock()
        audit.log_added.return_value = MagicMock()
        return audit

    @pytest.fixture
    def mock_repo(self):
        repo = MagicMock()
        repo.get_all.return_value = []
        return repo

    @pytest.mark.asyncio
    async def test_2a_create_report_button_creates_draft(
        self, mock_contractor_search, mock_auto_save, mock_repo
    ):
        """normal_user clicks 'Create Report' -> draft created."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="create_report")
        auth = MockHelpers.mock_authorization_service("normal_user")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "auto_save_service": mock_auto_save,
                "contractor_search": mock_contractor_search,
                "report_repository": mock_repo,
            },
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        mock_auto_save.save_draft.assert_called_once()
        update.callback_query.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_2b_state_awaiting_contractor_selection(
        self, mock_contractor_search, mock_auto_save, mock_repo
    ):
        """After clicking Create Report, state = awaiting_contractor_selection and keyboard shown."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="create_report")
        auth = MockHelpers.mock_authorization_service("normal_user")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "auto_save_service": mock_auto_save,
                "contractor_search": mock_contractor_search,
                "report_repository": mock_repo,
            },
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        assert context.user_data.get("state") == "awaiting_contractor_selection"
        call_kwargs = update.callback_query.edit_message_text.call_args[1]
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_2c_contractor_selection_sets_worker_state(self):
        """Clicking contractor -> state = awaiting_worker_count."""
        from app.bot.handlers.report_create import handle_contractor_selection
        from app.models.database import Contractor

        update = MockHelpers.mock_update(callback_data="select_contractor:0")
        context = MockHelpers.mock_context(
            user_data={
                "search_results": [("Test Co (Civil)", "0"), ("Another Co (Electrical)", "1")],
                "search_contractor_objects": [
                    Contractor(name="Test Co", type="Civil"),
                    Contractor(name="Another Co", type="Electrical"),
                ],
            },
        )

        await handle_contractor_selection(update, context)

        assert context.user_data["selected_contractor"] == "Test Co (Civil)"
        assert context.user_data["state"] == "awaiting_worker_count"
        update.callback_query.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_2d_worker_count_sets_zone_state(self, mock_contractor_search):
        """Sending worker number -> state = awaiting_zone."""
        from app.bot.handlers.report_create import handle_worker_count

        update = MockHelpers.mock_update(message_text="10")
        context = MockHelpers.mock_context(
            user_data={
                "state": "awaiting_worker_count",
                "selected_contractor": "Test Co (Civil)",
                "selected_contractor_raw_name": "Test Co",
            },
            bot_data={"contractor_search": mock_contractor_search},
        )

        await handle_worker_count(update, context)

        assert context.user_data["current_workers"] == 10
        assert context.user_data["state"] == "awaiting_zone"
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_2e_zone_selection_sets_details_state(self):
        """Selecting a zone -> state = awaiting_details."""
        from app.bot.handlers.report_create import handle_zone_selection

        update = MockHelpers.mock_update(callback_data="select_zone:Zone A")
        context = MockHelpers.mock_context(user_data={})

        await handle_zone_selection(update, context)

        assert context.user_data["current_zone"] == "Zone A"
        assert context.user_data["state"] == "awaiting_details"

    @pytest.mark.asyncio
    async def test_2e_zone_skip_sets_details_state(self):
        """Skipping zone -> state = awaiting_details, zone = None."""
        from app.bot.handlers.report_create import handle_zone_selection

        update = MockHelpers.mock_update(callback_data="select_zone:__skip__")
        context = MockHelpers.mock_context(user_data={})

        await handle_zone_selection(update, context)

        assert context.user_data["current_zone"] is None
        assert context.user_data["state"] == "awaiting_details"

    @pytest.mark.asyncio
    async def test_2f_details_triggers_audit_log(
        self, mock_auto_save, mock_contractor_search, mock_audit
    ):
        """Sending details -> audit.log_added() called, item persisted."""
        from app.bot.handlers.report_create import handle_details_input
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(message_text="Some work details")
        auth = MockHelpers.mock_authorization_service("normal_user")

        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            telegram_user="12345", id=1, items=[],
        )

        context = MockHelpers.mock_context(
            user_data={
                "state": "awaiting_details",
                "selected_contractor": "Test Co (Civil)",
                "selected_contractor_raw_name": "Test Co",
                "selected_contractor_type": "Civil",
                "selected_contractor_code": "Test Co",
                "current_workers": 10,
                "current_zone": "Zone A",
                "current_report": report,
            },
            bot_data={
                "auto_save_service": mock_auto_save,
                "authorization_service": auth,
                "audit_service": mock_audit,
                "contractor_search": mock_contractor_search,
            },
        )

        await handle_details_input(update, context)

        mock_audit.log_added.assert_called_once()
        mock_auto_save.auto_save_item.assert_called_once()

    @pytest.mark.asyncio
    async def test_2g_done_shows_summary(self):
        """/done shows summary with report details."""
        from app.bot.handlers.report_create import done_command
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(message_text="/done")
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            telegram_user="12345", id=1,
            items=[ReportItem(contractor="Test Co", workers=10, zone="Zone A")],
        )
        context = MockHelpers.mock_context(
            user_data={"current_report": report},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await done_command(update, context)

        assert context.user_data.get("state") == "idle"
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Summary" in call_text or "Report" in call_text


# =========================================================================
# Flow 3: Create Report (/new Command)
# =========================================================================


class TestFlow3_CreateReportNewCommand:
    """Test creating a report via the /new command."""

    @pytest.fixture
    def mock_suggestion_service(self):
        ss = MagicMock()
        ss.get_suggestions.return_value = []
        return ss

    @pytest.fixture
    def mock_auto_save(self):
        asv = MagicMock()
        from app.models.database import Report, ReportStatus
        asv.save_draft.return_value = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            telegram_user="12345", id=1,
        )
        return asv

    @pytest.fixture
    def mock_contractor_search(self):
        cs = MagicMock()
        from app.models.database import Contractor
        cs.get_all_contractors.return_value = [
            Contractor(name="Test Co", type="Civil"),
            Contractor(name="Another Co", type="Electrical"),
        ]
        cs.search.return_value = [Contractor(name="Test Co", type="Civil")]
        return cs

    @pytest.mark.asyncio
    async def test_3a_new_command_creates_draft(self, mock_suggestion_service, mock_auto_save):
        """/new command -> draft created, state = awaiting_contractor_name."""
        from app.bot.handlers.report_create import new_report_command

        update = MockHelpers.mock_update(message_text="/new")
        context = MockHelpers.mock_context(
            bot_data={
                "suggestion_service": mock_suggestion_service,
                "auto_save_service": mock_auto_save,
            },
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await new_report_command(update, context)

        mock_auto_save.save_draft.assert_called_once()
        assert context.user_data.get("state") == "awaiting_contractor_name"
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_3b_contractor_name_search_shows_results(self, mock_contractor_search):
        """Typing contractor name -> search results shown, state = awaiting_contractor_selection."""
        from app.bot.handlers.report_create import handle_contractor_name

        update = MockHelpers.mock_update(message_text="Test Co")
        context = MockHelpers.mock_context(
            user_data={"state": "awaiting_contractor_name"},
            bot_data={"contractor_search": mock_contractor_search},
        )

        await handle_contractor_name(update, context)

        mock_contractor_search.search.assert_called_once_with("Test Co", max_results=20)
        assert context.user_data.get("state") == "awaiting_contractor_selection"
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_3c_selection_onward_uses_same_handlers(self):
        """From contractor selection onward, same handlers as Flow 2 are used."""
        from app.bot.handlers.report_create import handle_contractor_selection
        from app.models.database import Contractor

        update = MockHelpers.mock_update(callback_data="select_contractor:0")
        context = MockHelpers.mock_context(
            user_data={
                "search_results": [("Test Co (Civil)", "0")],
                "search_contractor_objects": [Contractor(name="Test Co", type="Civil")],
            },
        )

        await handle_contractor_selection(update, context)

        assert context.user_data["selected_contractor"] == "Test Co (Civil)"
        assert context.user_data["state"] == "awaiting_worker_count"
        update.callback_query.edit_message_text.assert_called_once()


# =========================================================================
# Flow 4: Copy Yesterday
# =========================================================================


class TestFlow4_CopyYesterday:
    """Test the Copy Yesterday flow."""

    @pytest.fixture
    def mock_one_click(self):
        oc = MagicMock()
        from app.models.database import Report, ReportItem, ReportStatus
        report = Report(
            date="2026-07-13", day="Monday", status=ReportStatus.DRAFT,
            telegram_user="12345", id=1,
            items=[
                ReportItem(contractor="Yest Co", workers=5, zone="Zone B"),
            ],
        )
        oc.copy_yesterday.return_value = report
        return oc

    @pytest.fixture
    def mock_no_yesterday(self):
        oc = MagicMock()
        oc.copy_yesterday.return_value = None
        return oc

    @pytest.fixture
    def mock_auto_save(self):
        asv = MagicMock()
        from app.models.database import Report, ReportItem, ReportStatus
        asv.save_draft.side_effect = lambda report, tg_user: report
        return asv

    @pytest.fixture
    def mock_repo(self):
        repo = MagicMock()
        repo.get_all.return_value = []
        return repo

    @pytest.mark.asyncio
    async def test_4a_copy_yesterday_calls_service(
        self, mock_one_click, mock_auto_save, mock_repo
    ):
        """Clicking 'Copy Yesterday' -> one_click_yesterday_service.copy_yesterday() called."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="copy_yesterday")
        auth = MockHelpers.mock_authorization_service("normal_user")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "one_click_yesterday_service": mock_one_click,
                "auto_save_service": mock_auto_save,
                "report_repository": mock_repo,
            },
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        mock_one_click.copy_yesterday.assert_called_once()

    @pytest.mark.asyncio
    async def test_4b_draft_persisted(self, mock_one_click, mock_auto_save, mock_repo):
        """Draft persisted via auto_save_service.save_draft()."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="copy_yesterday")
        auth = MockHelpers.mock_authorization_service("normal_user")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "one_click_yesterday_service": mock_one_click,
                "auto_save_service": mock_auto_save,
                "report_repository": mock_repo,
            },
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        mock_auto_save.save_draft.assert_called_once()
        assert context.user_data.get("current_report") is not None

    @pytest.mark.asyncio
    async def test_4c_summary_shown_with_items(self, mock_one_click, mock_auto_save, mock_repo):
        """Summary shown with copied items."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="copy_yesterday")
        auth = MockHelpers.mock_authorization_service("normal_user")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "one_click_yesterday_service": mock_one_click,
                "auto_save_service": mock_auto_save,
                "report_repository": mock_repo,
            },
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Yest Co" in call_text
        assert "Copy" in call_text or "Copied" in call_text

    @pytest.mark.asyncio
    async def test_4d_no_yesterday_report(self, mock_no_yesterday, mock_auto_save, mock_repo):
        """No yesterday report -> 'No report from yesterday' message."""
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="copy_yesterday")
        auth = MockHelpers.mock_authorization_service("normal_user")
        context = MockHelpers.mock_context(
            bot_data={
                "authorization_service": auth,
                "one_click_yesterday_service": mock_no_yesterday,
                "auto_save_service": mock_auto_save,
                "report_repository": mock_repo,
            },
            user_data={},
        )

        with patch("app.utils.business_hours.is_business_hours", return_value=True):
            await handle_main_menu_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "No report" in call_text or "yesterday" in call_text.lower()
