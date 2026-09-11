"""Money-ledger tests (ticket-009): events, derived states, closure, disputes."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import hr as hr_handlers
from app.database.manager import DatabaseManager
from app.models.hr import HRRequestStatus
from app.repositories.hr_repository import HRRepository
from app.repositories.money_repository import MoneyRepository
from app.repositories.money_repository import MoneyRepository
from app.services.hr_service import HRService
from app.utils.exceptions import DatabaseError


@pytest.fixture(autouse=True)
def _site_env(monkeypatch):
    monkeypatch.setenv("SITE_ID", "site-a")


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "m.db"))
        yield HRService(HRRepository(manager), MoneyRepository(manager))
        manager.close_all()


def _approved_advance(service: HRService, amount=5000.0, site="site-a"):
    req = service.request_advance("u1", "User One", amount, "need", site_id=site)
    service.confirm_pm(req.id, "pm1", "PM", site_id=site)
    return service.decide_hr(req.id, "hr1", "HR", True,
                             deduction_month="2026-10", site_id=site)


def _approved_transport(service: HRService, site="site-a"):
    req = service.request_transport(
        "u1", "User One", 200, "visit", trip_date="2026-09-01", site_id=site)
    service.confirm_pm(req.id, "pm1", "PM", site_id=site)
    return service.decide_hr(req.id, "hr1", "HR", True, site_id=site)


class TestPayouts:
    def test_partial_then_full(self, service: HRService):
        req = _approved_advance(service)
        service.record_payout(req.id, 2000, "2026-09-05", "fin1", site_id="site-a")
        assert service.financial_status(req.id, site_id="site-a")["state"] == "partially_paid"
        service.record_payout(req.id, 3000, "2026-09-06", "fin1", site_id="site-a")
        status = service.financial_status(req.id, site_id="site-a")
        assert status["state"] == "paid"
        assert status["paid"] == 5000

    def test_payout_only_when_approved(self, service: HRService):
        req = service.request_advance("u1", "U", 100, "x", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.record_payout(req.id, 100, "2026-09-05", "fin1", site_id="site-a")

    def test_overpay_stays_auditable(self, service: HRService):
        req = _approved_advance(service, amount=1000.0)
        service.record_payout(req.id, 1500, "2026-09-05", "fin1", site_id="site-a")
        status = service.financial_status(req.id, site_id="site-a")
        assert status["paid"] == 1500
        assert status["state"] == "paid"  # sums decide; disputes flag the rest


class TestDeductions:
    def test_installments(self, service: HRService):
        req = _approved_advance(service)
        service.record_payout(req.id, 5000, "2026-09-05", "fin1", site_id="site-a")
        service.record_deduction(req.id, 2000, "2026-10", "pay1", site_id="site-a")
        status = service.financial_status(req.id, site_id="site-a")
        assert status["deduction_state"] == "partially_deducted"
        assert status["state"] != "closed"
        service.record_deduction(req.id, 2000, "2026-11", "pay1", site_id="site-a")
        service.record_deduction(req.id, 1000, "2026-12", "pay1", site_id="site-a")
        status = service.financial_status(req.id, site_id="site-a")
        assert status["deduction_state"] == "fully_deducted"
        assert status["state"] == "closed"

    def test_deduction_only_advances(self, service: HRService):
        req = _approved_transport(service)
        with pytest.raises(DatabaseError):
            service.record_deduction(req.id, 200, "2026-10", "pay1", site_id="site-a")

    def test_bad_period_rejected(self, service: HRService):
        req = _approved_advance(service)
        with pytest.raises(DatabaseError):
            service.record_deduction(req.id, 100, "October", "pay1", site_id="site-a")


class TestClosureTransport:
    def test_paid_closes(self, service: HRService):
        req = _approved_transport(service)
        service.record_payout(req.id, 200, "2026-09-05", "fin1", site_id="site-a")
        assert service.financial_status(req.id, site_id="site-a")["state"] == "closed"
        assert service.try_close(req.id, site_id="site-a") is True

    def test_try_close_false_when_open(self, service: HRService):
        req = _approved_transport(service)
        assert service.try_close(req.id, site_id="site-a") is False


class TestDisputes:
    def test_dispute_and_resolve(self, service: HRService):
        req = _approved_advance(service)
        service.dispute(req.id, "u1", "User One", "wrong amount", site_id="site-a")
        assert service.financial_status(req.id, site_id="site-a")["disputed"] is True
        assert service.financial_status(req.id, site_id="site-a")["state"] == "disputed"
        service.resolve_dispute(req.id, "hr1", "HR", "hr", "verified ok", site_id="site-a")
        assert service.financial_status(req.id, site_id="site-a")["disputed"] is False

    def test_dispute_needs_note(self, service: HRService):
        req = _approved_advance(service)
        with pytest.raises(DatabaseError):
            service.dispute(req.id, "u1", "U", "  ", site_id="site-a")

    def test_resolve_without_dispute_fails(self, service: HRService):
        req = _approved_advance(service)
        with pytest.raises(DatabaseError):
            service.resolve_dispute(req.id, "hr1", "HR", "hr", site_id="site-a")


class TestSiteIsolation:
    def test_events_scoped(self, service: HRService):
        req = _approved_advance(service)
        service.record_payout(req.id, 5000, "2026-09-05", "fin1", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.record_payout(req.id, 1, "2026-09-05", "fin1", site_id="site-b")


def _ctx(auth_role_caps, service):
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = "hr"
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in auth_role_caps)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth, "hr_service": service}
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


def _msg(text, user_id=777):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "Fin"
    update.effective_user.last_name = ""
    message = MagicMock(spec=Message)
    message.text = text
    message.reply_text = AsyncMock()
    message.photo = []
    update.message = message
    update.callback_query = None
    update.effective_message = message
    return update


class TestMoneyCommands:
    async def test_pay_command_records(self, service: HRService):
        req = _approved_advance(service)
        ctx = _ctx({"confirm_payout"}, service)
        await hr_handlers.pay_command(_msg("/hr_pay %d 5000 2026-09-05" % req.id), ctx)
        assert service.financial_status(req.id, site_id="site-a")["state"] == "paid"

    async def test_pay_refused_without_capability(self, service: HRService):
        req = _approved_advance(service)
        ctx = _ctx(set(), service)
        upd = _msg("/hr_pay %d 5000 2026-09-05" % req.id)
        await hr_handlers.pay_command(upd, ctx)
        assert "needs a finance" in upd.message.reply_text.call_args[0][0]

    async def test_deduct_command_records(self, service: HRService):
        req = _approved_advance(service)
        service.record_payout(req.id, 5000, "2026-09-05", "fin1", site_id="site-a")
        ctx = _ctx({"confirm_payroll_deduction"}, service)
        await hr_handlers.deduct_command(
            _msg("/hr_deduct %d 5000 2026-10" % req.id), ctx)
        status = service.financial_status(req.id, site_id="site-a")
        assert status["deduction_state"] == "fully_deducted"
        assert status["state"] == "closed"

    async def test_deduct_bad_usage(self, service: HRService):
        ctx = _ctx({"confirm_payroll_deduction"}, service)
        upd = _msg("/hr_deduct 1 100")
        await hr_handlers.deduct_command(upd, ctx)
        assert "Usage" in upd.message.reply_text.call_args[0][0]
