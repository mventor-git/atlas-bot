"""Trial-readiness fix regressions (forensic audit): OWNER view gate, HR SoD,
second-leg re-checks, assisted-confirm gating."""

import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.auth import capabilities as caps
from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.hr_repository import HRRepository
from app.services.hr_service import HRService
from app.utils.exceptions import DatabaseError


def mock_update(text="", user_id=111, callback_data=None):
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
    if callback_data is not None:
        query = MagicMock()
        query.data = callback_data
        query.edit_message_text = AsyncMock()
        query.answer = AsyncMock()
        update.callback_query = query
    else:
        update.callback_query = None
    update.effective_message = msg
    return update


def mock_auth(role="hr", sites=("site-a",), deny=()):
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session=None: session or sites[0])
    auth.sites_for_user.return_value = list(sites)

    def _has(chat_id, cap, site_id=None):
        if cap in deny:
            return False
        return cap in caps.for_role(role)

    auth.has_capability.side_effect = _has
    return auth


def mock_context(auth, bot_data=None, user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth}
    context.bot_data.update(bot_data or {})
    context.user_data = user_data or {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def hr_service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "hr.db"))
        yield HRService(HRRepository(manager))
        manager.close_all()


class TestOwnerViewGate:
    def _report(self):
        item = ReportItem(contractor="SecretCo", workers=7, zone="Zone9",
                          details="Night pour")
        return Report(date=date.today().isoformat(), day="Monday",
                      status=ReportStatus.FINAL, items=[item], id=1,
                      site_id="site-a",
                      finalized_at="2026-09-01T10:00:00")

    async def test_owner_gets_summary_only(self):
        from app.bot.handlers.start import handle_main_menu_callback

        update = mock_update(callback_data="view_report")
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = self._report()
        ctx = mock_context(mock_auth("viewer"),
                           {"report_repository": repo_mock,
                            "audit_service": MagicMock()})
        await handle_main_menu_callback(update, ctx)
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "SecretCo" not in text
        assert "Zone9" not in text
        assert "Night pour" not in text

    async def test_site_member_keeps_detail(self):
        from app.bot.handlers.start import handle_main_menu_callback

        update = mock_update(callback_data="view_report")
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = self._report()
        ctx = mock_context(mock_auth("normal_user"),
                           {"report_repository": repo_mock,
                            "audit_service": MagicMock()})
        await handle_main_menu_callback(update, ctx)
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "SecretCo" in text and "Zone9" in text


class TestHRSelfApproval:
    def test_cannot_confirm_own_request(self, hr_service):
        req = hr_service.request_advance("pm1", "PM", 500, "need",
                                         site_id="site-a")
        with pytest.raises(DatabaseError, match="own request"):
            hr_service.confirm_pm(req.id, "pm1", "PM", site_id="site-a")

    def test_cannot_decide_own_request(self, hr_service):
        req = hr_service.request_advance("hr1", "HR", 500, "need",
                                         site_id="site-a")
        hr_service.confirm_pm(req.id, "pm2", "PM", site_id="site-a")
        with pytest.raises(DatabaseError, match="own request"):
            hr_service.decide_hr(req.id, "hr1", "HR", True,
                                 deduction_month="2026-10", site_id="site-a")

    def test_distinct_actors_still_work(self, hr_service):
        req = hr_service.request_advance("u1", "U", 500, "need",
                                         site_id="site-a")
        hr_service.confirm_pm(req.id, "pm1", "PM", site_id="site-a")
        decided = hr_service.decide_hr(req.id, "hr1", "HR", True,
                                       deduction_month="2026-10",
                                       site_id="site-a")
        assert decided.status == "approved"


class TestSecondLegRechecks:
    async def test_salary_csv_denied_after_revoke(self, hr_service):
        from app.bot.handlers import payroll as payroll_handlers
        from app.database.manager import DatabaseManager as DM
        from app.repositories.payroll_repository import PayrollRepository
        from app.repositories.user_repository import UserRepository
        from app.services.payroll_service import PayrollService

        with tempfile.TemporaryDirectory() as tmp:
            manager = DM(str(Path(tmp) / "p.db"))
            try:
                users = UserRepository(manager)
                from app.models.database import User
                users.upsert(User(chat_id="u1", role="normal_user"))
                service = PayrollService(PayrollRepository(manager), users)
                ctx = mock_context(
                    mock_auth("hr", deny=("manage_payroll",)),
                    {"payroll_service": service},
                    {"state": "awaiting_salary_csv"})
                update = mock_update("u1,9999", user_id=222)
                await payroll_handlers.handle_salary_csv(update, ctx)
                assert "HQ rights" in \
                    update.message.reply_text.call_args[0][0]
                assert users.get_by_chat_id("u1").monthly_salary is None
            finally:
                manager.close_all()

    async def test_hr_reject_note_denied(self, hr_service):
        from app.bot.handlers import hr as hr_handlers

        ctx = mock_context(
            mock_auth("hr", deny=("decide_hr_request",)),
            {"hr_service": hr_service},
            {"hr_reject_id": 1, "hr_reject_site": "site-a",
             "state": "awaiting_reject_note"})
        update = mock_update("nope", user_id=77)
        await hr_handlers.handle_reject_note(update, ctx)
        assert "HR account" in update.message.reply_text.call_args[0][0]

    async def test_hr_deduction_month_denied(self, hr_service):
        from app.bot.handlers import hr as hr_handlers

        ctx = mock_context(
            mock_auth("hr", deny=("decide_hr_request",)),
            {"hr_service": hr_service},
            {"hr_approve_id": 1, "hr_approve_site": "site-a",
             "state": "awaiting_deduction_month"})
        update = mock_update("2026-10", user_id=77)
        await hr_handlers.handle_deduction_month(update, ctx)
        assert "HR account" in update.message.reply_text.call_args[0][0]

    async def test_hr_delegate_target_denied(self, hr_service):
        from app.bot.handlers import hr as hr_handlers

        req = hr_service.request_advance("u1", "U", 500, "need",
                                         site_id="site-a")
        ctx = mock_context(
            mock_auth("project_manager", deny=("delegate_hr_request",)),
            {"hr_service": hr_service},
            {"hr_delegate_id": req.id, "hr_delegate_site": "site-a",
             "state": "awaiting_delegate_target"})
        update = mock_update("hr9", user_id=78)
        await hr_handlers.handle_delegate_target(update, ctx)
        assert "PM account" in update.message.reply_text.call_args[0][0]

    async def test_disc_note_denied(self):
        from app.bot.handlers import discipline as disc_handlers

        ctx = mock_context(
            mock_auth("hr", deny=("approve_disciplinary_action",)),
            {},
            {"disc_decide_id": 1, "disc_decide_site": "site-a",
             "state": "awaiting_disc_note"})
        update = mock_update("warn | note", user_id=77)
        await disc_handlers.handle_disc_note(update, ctx)
        assert "approval rights" in update.message.reply_text.call_args[0][0]


class TestAssistedConfirmGating:
    async def test_assisted_stays_pending_without_confirmer(self):
        from app.bot.handlers import attendance as att_handlers

        event = SimpleNamespace(id=7, chat_id="u9", method="assisted",
                                site_id="site-a", event_date="2026-09-13")
        app_config = MagicMock()
        app_config.sites = []
        ctx = mock_context(mock_auth("normal_user"), {
            "app_config": app_config,
            # NOTE: no attendance_service on purpose: any auto-confirm
            # attempt raises KeyError and fails this test.
        })
        update = mock_update(user_id=111)
        await att_handlers._route_to_confirmer(update, ctx, event)
        # Reached here without KeyError => no confirm attempted.
