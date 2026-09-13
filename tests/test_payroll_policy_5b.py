"""Payroll policy + calculation tests (Stage 5B): versioning, snapshot,
approved-input linkage, corrections, explanation, privacy.

Business values are NOT asserted as company truth; tests assert
configurability, provenance, reproducibility, and boundaries.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.auth import capabilities as caps
from app.bot.handlers import payroll as payroll_handlers
from app.database.manager import DatabaseManager
from app.models.database import User
from app.models.payroll import PayrollRunStatus
from app.repositories.discipline_repository import DisciplineRepository
from app.repositories.hr_repository import HRRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.money_repository import MoneyRepository
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.services.discipline_service import DisciplineService
from app.services.hr_service import HRService
from app.services.payroll_service import PayrollService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def stack():
    """Full wiring: payroll + HR + money + discipline + memberships."""
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "p.db"))
        users = UserRepository(manager)
        members = MembershipRepository(manager)
        hr_repo, money_repo = HRRepository(manager), MoneyRepository(manager)
        users.upsert(User(chat_id="u1", role="normal_user", site_id="site-a"))
        users.upsert(User(chat_id="u2", role="normal_user", site_id="site-a"))
        members.grant("u1", "site-a")
        members.grant("u2", "site-a")
        users.set_salary("u1", 12000.0, set_by="hq1",
                           effective_from="2026-01-01")
        users.set_salary("u2", 8000.0, set_by="hq1",
                         effective_from="2026-01-01")
        service = PayrollService(PayrollRepository(manager), users,
                                 membership_repo=members,
                                 hr_repo=hr_repo, money_repo=money_repo)
        hr = HRService(hr_repo, money_repo)
        disc = DisciplineService(DisciplineRepository(manager))
        yield {"service": service, "users": users, "hr": hr,
               "disc": disc, "manager": manager}
        manager.close_all()


def approve_ot(hr, chat_id, day, hours, site="site-a"):
    req = hr.request_overtime(chat_id, "N", day, hours, site_id=site)
    hr.confirm_pm(req.id, "pm1", "PM", site_id=site)
    return hr.decide_hr(req.id, "hr1", "HR", True, site_id=site)


def approve_advance(hr, chat_id, amount, site="site-a"):
    req = hr.request_advance(chat_id, "N", amount, "need", site_id=site)
    hr.confirm_pm(req.id, "pm1", "PM", site_id=site)
    return hr.decide_hr(req.id, "hr1", "HR", True, deduction_month="2026-09",
                        site_id=site)


def make_update(user_id="hq1", text=""):
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
    update.callback_query = None
    update.effective_message = msg
    return update


def make_context(auth, service):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth,
                        "payroll_service": service}
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


def make_auth(role="hr"):
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session_site=None: session_site or "site-a")
    auth.sites_for_user.return_value = ["site-a"]
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    return auth


class TestPolicyVersioning:
    def test_versions_append_never_rewrite(self, stack):
        service = stack["service"]
        v1 = service.set_policy("site-a", "hq1", reason="first")
        v2 = service.set_policy("site-a", "hq1", standard_hours=208.0,
                                reason="second")
        assert (v1.version, v2.version) == (1, 2)
        policies = service._repo.policies_for("site-a")
        assert [(p.version, p.standard_hours) for p in policies] == [
            (1, 240.0), (2, 208.0)]
        assert policies[0].reason == "first"

    def test_unsupported_modes_refused(self, stack):
        service = stack["service"]
        with pytest.raises(DatabaseError):
            service.set_policy("site-a", "hq1", hours_basis="workdays")
        with pytest.raises(DatabaseError):
            service.set_policy("site-a", "hq1", rounding="floor")
        with pytest.raises(DatabaseError):
            service.set_policy("site-a", "hq1", standard_hours=0)
        with pytest.raises(DatabaseError):
            service.set_policy("site-a", "hq1",
                               effective_from="next-friday")

    def test_active_policy_by_effective_date(self, stack):
        service = stack["service"]
        service.set_policy("site-a", "hq1", effective_from="2026-01-01")
        service.set_policy("site-a", "hq1", standard_hours=208.0,
                           effective_from="2026-07-01")
        assert service.active_policy("site-a", "2026-03").version == 1
        assert service.active_policy("site-a", "2026-09").version == 2
        assert service.active_policy("site-a", "2025-12") is None

    def test_capability_gates_policy(self):
        assert caps.is_known("manage_payroll_policy")
        assert "manage_payroll_policy" in caps.for_role("hr")
        assert "manage_payroll_policy" in caps.for_role("superadmin")
        assert "manage_payroll_policy" not in caps.for_role("project_manager")
        assert "manage_payroll_policy" not in caps.for_role("normal_user")


class TestPolicySnapshot:
    def test_no_policy_records_system_default(self, stack):
        service = stack["service"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        snap = json.loads(run.policy_snapshot)
        assert snap["origin"] == "system_default"
        assert run.policy_version is None
        assert (run.standard_hours, run.ot_multiplier) == (240.0, 1.5)

    def test_run_freezes_policy_values(self, stack):
        service = stack["service"]
        service.set_policy("site-a", "hq1", standard_hours=208.0,
                           ot_multiplier=2.0, effective_from="2026-01-01")
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        assert run.policy_version == 1
        assert (run.standard_hours, run.ot_multiplier) == (208.0, 2.0)
        line = service.add_line(run.id, "u1", 12000.0, 8.0, site_id="site-a")
        assert line.ot_amount == round(8 * (12000.0 / 208.0) * 2.0, 2)

    def test_policy_change_never_rewrites_old_run(self, stack):
        service = stack["service"]
        service.set_policy("site-a", "hq1", effective_from="2026-01-01")
        old = service.create_run("2026-09", "hq1", site_id="site-a")
        old_snap = old.policy_snapshot
        service.set_policy("site-a", "hq1", standard_hours=208.0,
                           effective_from="2026-10-01")
        new = service.create_run("2026-10", "hq1", site_id="site-a")
        assert service.get_run("2026-09",
                               site_id="site-a").policy_snapshot == old_snap
        assert new.standard_hours == 208.0 and new.policy_version == 2
        assert old.standard_hours == 240.0 and old.policy_version == 1


class TestSalaryReproducibility:
    def test_mid_year_raise_keeps_old_line(self, stack):
        service, users = stack["service"], stack["users"]
        sep = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(sep.id, "u1", 12000.0, 10.0, 2000.0, 500.0,
                                site_id="site-a")
        users.set_salary("u1", 15000.0, set_by="hq1", reason="raise")
        oct_run = service.create_run("2026-10", "hq1", site_id="site-a")
        newline = service.add_line(oct_run.id, "u1", 15000.0,
                                   site_id="site-a")
        kept = service.lines(sep)[0]
        assert (kept.base_pay, kept.net) == (12000.0, line.net)
        assert kept.salary_history_id != newline.salary_history_id

    def test_stored_net_recomputes_from_snapshot(self, stack):
        service = stack["service"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, 10.0, 2000.0, 500.0,
                                site_id="site-a")
        snap = json.loads(run.policy_snapshot)
        ot, net = service.compute_net(
            line.base_pay, line.ot_hours, run.standard_hours,
            run.ot_multiplier, line.advances, line.deductions,
            rounding=snap["rounding"])
        assert (ot, net) == (line.ot_amount, line.net)


class TestApprovedOT:
    def test_only_approved_counts(self, stack):
        service, hr = stack["service"], stack["hr"]
        approve_ot(hr, "u1", "2026-09-05", 4.0)
        pending = hr.request_overtime("u1", "N", "2026-09-06", 3.0,
                                      site_id="site-a")
        assert pending.status == "pending"
        result = service.approved_ot_for("u1", "site-a", "2026-09")
        assert result["hours"] == 4.0 and len(result["request_ids"]) == 1

    def test_rejected_and_other_period_excluded(self, stack):
        service, hr = stack["service"], stack["hr"]
        req = hr.request_overtime("u1", "N", "2026-09-05", 4.0,
                                  site_id="site-a")
        hr.confirm_pm(req.id, "pm1", "PM", site_id="site-a")
        hr.decide_hr(req.id, "hr1", "HR", False, site_id="site-a")
        approve_ot(hr, "u1", "2026-08-05", 6.0)
        assert service.approved_ot_for("u1", "site-a", "2026-09")["hours"] == 0.0

    def test_approved_apply_cites_refs(self, stack):
        service, hr = stack["service"], stack["hr"]
        req = approve_ot(hr, "u1", "2026-09-05", 4.0)
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, 4.0, site_id="site-a",
                                ot_source="approved",
                                ot_refs=str(req.id))
        assert line.ot_source == "approved" and line.ot_refs == str(req.id)

    def test_approved_without_refs_refused(self, stack):
        service = stack["service"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError, match="must cite record refs"):
            service.add_line(run.id, "u1", 12000.0, 4.0, site_id="site-a",
                             ot_source="approved")


class TestAdvanceLinkage:
    def test_recorded_deductions_suggested_per_period(self, stack):
        service, hr = stack["service"], stack["hr"]
        req = approve_advance(hr, "u1", 5000.0)
        hr.record_deduction(req.id, 2000.0, "2026-10", "hq1", site_id="site-a")
        hr.record_deduction(req.id, 2000.0, "2026-11", "hq1", site_id="site-a")
        assert service.period_deductions_for("u1", "site-a",
                                             "2026-10")["total"] == 2000.0
        assert service.period_deductions_for("u1", "site-a",
                                             "2026-11")["total"] == 2000.0
        assert service.period_deductions_for("u1", "site-a",
                                             "2026-12")["total"] == 0.0

    def test_unrecorded_advance_counts_zero(self, stack):
        service, hr = stack["service"], stack["hr"]
        approve_advance(hr, "u1", 5000.0)  # approved, never deducted
        assert service.period_deductions_for("u1", "site-a",
                                             "2026-10") == {
                                                 "total": 0.0, "event_ids": []}

    def test_request_and_approval_alone_move_nothing(self, stack):
        service, hr = stack["service"], stack["hr"]
        req = hr.request_advance("u1", "N", 5000.0, "need", site_id="site-a")
        assert service.period_deductions_for("u1", "site-a",
                                             "2026-10")["total"] == 0.0
        hr.confirm_pm(req.id, "pm1", "PM", site_id="site-a")
        hr.decide_hr(req.id, "hr1", "HR", True, deduction_month="2026-10",
                     site_id="site-a")
        assert service.period_deductions_for("u1", "site-a",
                                             "2026-10")["total"] == 0.0


class TestDisciplineBoundary:
    def test_decided_case_moves_no_payroll(self, stack):
        service, disc = stack["service"], stack["disc"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        case = disc.file("u1", "hq1", "late twice", site_id="site-a")
        disc.review(case.id, "hq2", site_id="site-a")
        disc.decide(case.id, "hq2", "warning", "first warning",
                    site_id="site-a")
        kept = service.lines(run)[0]
        assert (kept.net, kept.deductions) == (line.net, 0.0)
        assert "discipline" not in service.explain_line(
            run.id, line.id, site_id="site-a")


class TestCorrections:
    def _exported(self, service):
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        service.mark_exported(run.id, site_id="site-a")
        return run, line

    def test_draft_adjustment_refused(self, stack):
        service = stack["service"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        with pytest.raises(DatabaseError, match="exported runs only"):
            service.add_adjustment(run.id, "u1", -500.0, "fix", "hq1",
                                   site_id="site-a")

    def test_reason_required(self, stack):
        service = stack["service"]
        run, _ = self._exported(service)
        with pytest.raises(DatabaseError, match="reason is required"):
            service.add_adjustment(run.id, "u1", -500.0, "  ", "hq1",
                                   site_id="site-a")

    def test_adjustment_preserves_locked_line(self, stack):
        service = stack["service"]
        run, line = self._exported(service)
        service.add_adjustment(run.id, "u1", -500.0, "overcounted OT",
                               "hq1", site_id="site-a")
        kept = service.lines(run)[0]
        assert kept.net == line.net == 12000.0
        assert service.adjusted_net(run.id, "u1", site_id="site-a") == 11500.0

    def test_unknown_subject_refused(self, stack):
        service = stack["service"]
        run, _ = self._exported(service)
        with pytest.raises(DatabaseError):
            service.add_adjustment(run.id, "ghost", 100.0, "fix", "hq1",
                                   site_id="site-a")

    async def test_mypay_shows_correction(self, stack):
        service = stack["service"]
        run, _ = self._exported(service)
        service.add_adjustment(run.id, "u1", -500.0, "overcounted OT",
                               "hq1", site_id="site-a")
        ctx = make_context(make_auth("normal_user"), service)
        update = make_update("u1", "/mypay 2026-09")
        await payroll_handlers.mypay_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "Corrections" in text and "11500" in text


class TestMultiSiteNoDouble:
    def test_same_employee_two_site_runs_stay_separate(self, stack):
        service = stack["service"]
        run_a = service.create_run("2026-09", "hq1", site_id="site-a")
        members = service._memberships
        members.grant("u1", "site-b")
        run_b = service.create_run("2026-09", "hq1", site_id="site-b")
        service.add_line(run_a.id, "u1", 12000.0, site_id="site-a")
        service.add_line(run_b.id, "u1", 12000.0, site_id="site-b")
        assert len(service.lines(run_a)) == 1
        assert len(service.lines(run_b)) == 1


class TestExplanation:
    def test_explain_covers_inputs_and_policy(self, stack):
        service, hr = stack["service"], stack["hr"]
        req = approve_ot(hr, "u1", "2026-09-05", 4.0)
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, 4.0, 1000.0, 0.0,
                                site_id="site-a", ot_source="approved",
                                ot_refs=str(req.id))
        expl = service.explain_line(run.id, line.id, site_id="site-a")
        assert expl["base_pay"] == 12000.0
        assert expl["salary_history_id"] == line.salary_history_id
        assert expl["ot_refs"] == str(req.id)
        assert expl["deductions_source"] == "manual"
        assert expl["net"] == line.net
        assert expl["adjusted_net"] == line.net
        assert expl["policy_origin"] == "system_default"


class TestPolicyHandlers:
    async def test_policy_view_defaults(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth(), service)
        update = make_update("hq1", "/payroll_policy 2026-09")
        await payroll_handlers.payroll_policy_command(update, ctx)
        assert "system defaults" in \
            update.effective_message.reply_text.call_args[0][0]

    async def test_policy_set_and_view(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth(), service)
        await payroll_handlers.payroll_policy_set_command(
            make_update("hq1", "/payroll_policy_set 2026-07-01 208 2.0 summer"),
            ctx)
        update = make_update("hq1", "/payroll_policy 2026-09")
        await payroll_handlers.payroll_policy_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "v1" in text and "208" in text

    async def test_policy_set_gated(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth("project_manager"), service)
        update = make_update("pm1", "/payroll_policy_set 2026-07-01 208 2.0")
        await payroll_handlers.payroll_policy_set_command(update, ctx)
        assert "policy rights" in \
            update.effective_message.reply_text.call_args[0][0]
        assert service._repo.policies_for("site-a") == []

    async def test_adjust_flow(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth(), service)
        await payroll_handlers.payroll_build_command(
            make_update("hq1", "/payroll_build 2026-09"), ctx)
        await payroll_handlers.payroll_add_command(
            make_update("hq1", "/payroll_add 2026-09 u1"), ctx)
        await payroll_handlers.payroll_export_command(
            make_update("hq1", "/payroll_export 2026-09"), ctx)
        update = make_update("hq1", "/payroll_adjust 2026-09 u1 -500 fix ot")
        await payroll_handlers.payroll_adjust_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "11500" in text
