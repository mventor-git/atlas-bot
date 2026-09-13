"""Payroll integration + trial scenarios (Stage 5C): rule-B salary, E2E A-K,
auth matrix, exceptions, self-service, HQ inspection, audit trail."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.auth import capabilities as caps
from app.bot.handlers import payroll as payroll_handlers
from app.database.manager import DatabaseManager
from app.models.attendance import AttendanceDay
from app.models.database import User
from app.models.payroll import PayrollRunStatus
from app.repositories.attendance_day_repository import (
    AttendanceDayRepository)
from app.repositories.hr_repository import HRRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.money_repository import MoneyRepository
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.services.discipline_service import DisciplineService
from app.services.event_log_service import EventLogService
from app.services.hr_service import HRService
from app.services.notification_templates import build as build_notice
from app.services.payroll_service import PayrollService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def stack():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "p.db"))
        users = UserRepository(manager)
        members = MembershipRepository(manager)
        hr_repo, money_repo = HRRepository(manager), MoneyRepository(manager)
        day_repo = AttendanceDayRepository(manager)
        users.upsert(User(chat_id="u1", role="normal_user", site_id="site-a"))
        users.upsert(User(chat_id="u2", role="normal_user", site_id="site-a"))
        members.grant("u1", "site-a")
        members.grant("u2", "site-a")
        users.set_salary("u1", 12000.0, set_by="hq1",
                         effective_from="2026-01-01")
        users.set_salary("u2", 8000.0, set_by="hq1",
                         effective_from="2026-01-01")
        service = PayrollService(
            PayrollRepository(manager), users, membership_repo=members,
            hr_repo=hr_repo, money_repo=money_repo, day_repo=day_repo)
        yield {"service": service, "users": users, "members": members,
               "hr": HRService(hr_repo, money_repo), "days": day_repo,
               "manager": manager, "events": EventLogService(manager)}
        manager.close_all()


def approve_ot(hr, chat_id, day, hours, site="site-a"):
    req = hr.request_overtime(chat_id, "N", day, hours, site_id=site)
    hr.confirm_pm(req.id, "pm1", "PM", site_id=site)
    return hr.decide_hr(req.id, "hr1", "HR", True, site_id=site)


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


def make_context(auth, service, events=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"authorization_service": auth,
                        "payroll_service": service}
    if events is not None:
        context.bot_data["event_log_service"] = events
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


def make_auth(role="hr", sites=("site-a",), deny_payroll_view=False):
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session_site=None: session_site or "site-a")
    auth.sites_for_user.return_value = list(sites)

    def _has(chat_id, cap, site_id=None):
        if cap == "view_own_payroll" and deny_payroll_view:
            return False
        return cap in caps.for_role(role)

    auth.has_capability.side_effect = _has
    return auth


class TestSalaryRuleB:
    def test_raise_before_period_applies(self, stack):
        service, users = stack["service"], stack["users"]
        users.set_salary("u1", 13000.0, set_by="hq1",
                         effective_from="2026-08-15")
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 13000.0, site_id="site-a")
        assert line.base_pay == 13000.0

    def test_raise_inside_period_applies_next_period(self, stack):
        service, users = stack["service"], stack["users"]
        users.set_salary("u1", 13000.0, set_by="hq1",
                         effective_from="2026-09-15")
        sep = service.create_run("2026-09", "hq1", site_id="site-a")
        sep_line = service.add_line(sep.id, "u1", 12000.0, site_id="site-a")
        assert sep_line.salary_history_id == 1  # Jan row, not the raise
        oct_run = service.create_run("2026-10", "hq1", site_id="site-a")
        oct_line = service.add_line(oct_run.id, "u1", 13000.0,
                                    site_id="site-a")
        assert oct_line.salary_history_id == 3

    def test_raise_after_period_keeps_old(self, stack):
        service, users = stack["service"], stack["users"]
        users.set_salary("u1", 13000.0, set_by="hq1",
                         effective_from="2026-10-01")
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        assert line.salary_history_id == 1

    def test_gap_blocks_with_clear_error(self, stack):
        service, users = stack["service"], stack["users"]
        users.upsert(User(chat_id="u9", role="normal_user",
                          site_id="site-a"))
        stack["members"].grant("u9", "site-a")
        users.set_salary("u9", 9000.0, set_by="hq1",
                         effective_from="2026-10-01")
        with pytest.raises(DatabaseError, match="No salary effective"):
            service.build_run("2026-09", "hq1", [{"chat_id": "u9"}],
                              site_id="site-a")

    def test_retroactive_row_spares_locked_run(self, stack):
        service, users = stack["service"], stack["users"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        service.mark_exported(run.id, site_id="site-a")
        users.set_salary("u1", 11000.0, set_by="hq1",
                         effective_from="2026-08-01",
                         reason="backdated correction")
        kept = service.lines(run)[0]
        assert (kept.base_pay, kept.salary_history_id) == (
            line.base_pay, line.salary_history_id)
        fresh = service.create_run("2026-08", "hq1", site_id="site-a")
        backdated = service.add_line(fresh.id, "u1", 11000.0,
                                     site_id="site-a")
        assert backdated.salary_history_id == 3


class TestScenarioA:
    def test_normal_employee_explains(self, stack):
        service = stack["service"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        assert line.net == 12000.0
        expl = service.explain_line(run.id, line.id, site_id="site-a")
        assert expl["base_pay"] == 12000.0
        assert expl["salary_history_id"] == 1
        assert expl["policy_origin"] == "system_default"
        assert expl["adjusted_net"] == 12000.0


class TestScenarioCE:
    def test_approved_ot_suggest_apply_provenance(self, stack):
        service, hr = stack["service"], stack["hr"]
        req = approve_ot(hr, "u1", "2026-09-05", 4.0)
        sug = service.approved_ot_for("u1", "site-a", "2026-09")
        assert sug == {"hours": 4.0, "request_ids": [req.id],
                       "by_site": {"site-a": 4.0}}
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, sug["hours"],
                                site_id="site-a", ot_source="approved",
                                ot_refs=",".join(map(str, sug["request_ids"])))
        assert line.ot_amount == round(4 * (12000.0 / 240.0) * 1.5, 2)
        assert line.ot_refs == str(req.id)

    def test_advance_installments_per_period(self, stack):
        service, hr = stack["service"], stack["hr"]
        req = hr.request_advance("u1", "N", 5000.0, "need", site_id="site-a")
        hr.confirm_pm(req.id, "pm1", "PM", site_id="site-a")
        hr.decide_hr(req.id, "hr1", "HR", True, deduction_month="2026-10",
                     site_id="site-a")
        hr.record_deduction(req.id, 2000.0, "2026-10", "hq1",
                            site_id="site-a")
        hr.record_deduction(req.id, 1000.0, "2026-11", "hq1",
                            site_id="site-a")
        oct_run = service.create_run("2026-10", "hq1", site_id="site-a")
        sug = service.period_deductions_for("u1", "site-a", "2026-10")
        oct_line = service.add_line(
            oct_run.id, "u1", 12000.0, 0.0, 0.0, sug["total"],
            site_id="site-a", deductions_source="approved",
            deductions_refs=",".join(map(str, sug["event_ids"])))
        assert oct_line.deductions == 2000.0 and oct_line.net == 10000.0
        nov_run = service.create_run("2026-11", "hq1", site_id="site-a")
        nov_sug = service.period_deductions_for("u1", "site-a", "2026-11")
        nov_line = service.add_line(
            nov_run.id, "u1", 12000.0, 0.0, 0.0, nov_sug["total"],
            site_id="site-a", deductions_source="approved",
            deductions_refs=",".join(map(str, nov_sug["event_ids"])))
        assert nov_line.net == 11000.0


class TestScenarioF:
    def test_disputed_and_resolved_create_no_deduction(self, stack):
        service, days = stack["service"], stack["days"]
        days.add_day(AttendanceDay(chat_id="u1", day_date="2026-09-04",
                                   site_id="site-a", status="resolved",
                                   verdict="absent",
                                   resolution_note="no show",
                                   resolved_by="pm1"))
        days.add_day(AttendanceDay(chat_id="u1", day_date="2026-09-05",
                                   site_id="site-a", status="disputed",
                                   verdict="absent",
                                   dispute_note="contested"))
        summary = service.attendance_summary_for("u1", "site-a", "2026-09")
        assert summary == {"resolved_by_verdict": {"absent": 1},
                           "open_days": 1}
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        assert line.net == 12000.0  # no automatic attendance deduction


class TestScenarioG:
    def test_filing_zero_effect_authorized_path_is_adjustment(self, stack):
        from app.repositories.discipline_repository import (
            DisciplineRepository)

        service = stack["service"]
        disc = DisciplineService(DisciplineRepository(stack["manager"]))
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        case = disc.file("u1", "hq1", "misconduct", site_id="site-a")
        disc.review(case.id, "hq2", site_id="site-a")
        disc.decide(case.id, "hq2", "fine 500", "HR decision",
                    site_id="site-a")
        assert service.lines(run)[0].net == line.net
        service.mark_exported(run.id, site_id="site-a")
        service.add_adjustment(run.id, "u1", -500.0,
                               "HR decision #{}: fine 500".format(case.id),
                               "hq2", site_id="site-a")
        assert service.adjusted_net(run.id, "u1",
                                    site_id="site-a") == 11500.0


class TestScenarioH:
    async def test_handler_cross_site_add_denied(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth(), service)
        await payroll_handlers.payroll_build_command(
            make_update("hq1", "/payroll_build 2026-09"), ctx)
        update = make_update("hq1", "/payroll_add 2026-09 u2")
        service._memberships.revoke("u2", "site-a")
        await payroll_handlers.payroll_add_command(update, ctx)
        assert "Could not add" in \
            update.effective_message.reply_text.call_args[0][0]


class TestScenarioI:
    async def test_multisite_mypay_combined(self, stack):
        service, members = stack["service"], stack["members"]
        members.grant("u1", "site-b")
        users = stack["users"]
        users.upsert(User(chat_id="u1", role="normal_user", site_id="site-a"))
        run_a = service.create_run("2026-09", "hq1", site_id="site-a")
        service.add_line(run_a.id, "u1", 12000.0, site_id="site-a")
        run_b = service.create_run("2026-09", "hq1", site_id="site-b")
        service.add_line(run_b.id, "u1", 12000.0, site_id="site-b")
        auth = make_auth(sites=("site-a", "site-b"))
        auth.resolve_active_site.side_effect = (
            lambda chat_id, session_site=None: session_site or "site-a")
        ctx = make_context(auth, service)
        update = make_update("u1", "/mypay 2026-09")
        await payroll_handlers.mypay_command(update, ctx)
        assert update.effective_message.reply_text.call_count == 3
        combined = update.effective_message.reply_text.call_args[0][0]
        assert "Combined across 2 sites" in combined and "24000" in combined


class TestAuthMatrix:
    def test_manipulated_run_site_denied(self, stack):
        service = stack["service"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError, match="not found in this site"):
            service.add_line(run.id, "u1", 12000.0, site_id="site-b")

    def test_line_from_other_run_refused(self, stack):
        service = stack["service"]
        run_a = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run_a.id, "u1", 12000.0, site_id="site-a")
        run_b = service.create_run("2026-10", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError, match="not in run"):
            service.update_line(line.id, run_b.id, 12000.0, 0.0, 0.0, 0.0,
                                site_id="site-a")

    def test_manipulated_site_param_cannot_move_run(self, stack):
        service = stack["service"]
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.mark_exported(run.id, site_id="site-b")
        assert service.get_run("2026-09",
                               site_id="site-a").status == "draft"

    async def test_revoked_user_mypay_denied(self, stack):
        service, members = stack["service"], stack["members"]
        members.revoke("u1", "site-a")
        auth = make_auth("normal_user", sites=())
        ctx = make_context(auth, service)
        update = make_update("u1", "/mypay 2026-09")
        await payroll_handlers.mypay_command(update, ctx)
        assert "approved account" in \
            update.effective_message.reply_text.call_args[0][0]

    async def test_mypay_without_capability_denied(self, stack):
        service = stack["service"]
        auth = make_auth("normal_user", deny_payroll_view=True)
        ctx = make_context(auth, service)
        update = make_update("u1", "/mypay 2026-09")
        await payroll_handlers.mypay_command(update, ctx)
        assert "approved account" in \
            update.effective_message.reply_text.call_args[0][0]


class TestExceptions:
    async def test_negative_net_flagged_and_noticed(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth(), service)
        await payroll_handlers.payroll_build_command(
            make_update("hq1", "/payroll_build 2026-09"), ctx)
        update = make_update("hq1", "/payroll_add 2026-09 u1 0 0 20000")
        await payroll_handlers.payroll_add_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "requires review" in text
        notice = build_notice("payroll_exception", period="2026-09",
                              site="site-a", note="negative net for `u1`")
        assert "review" in notice.lower()

    def test_correction_notice_builds(self):
        notice = build_notice("payroll_corrected", period="2026-09",
                              site="site-a", note="-500 for `u1`")
        assert "correction" in notice.lower()

    async def test_view_flags_negative_lines(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth(), service)
        await payroll_handlers.payroll_build_command(
            make_update("hq1", "/payroll_build 2026-09"), ctx)
        await payroll_handlers.payroll_add_command(
            make_update("hq1", "/payroll_add 2026-09 u1 0 0 20000"), ctx)
        update = make_update("hq1", "/payroll_view 2026-09")
        await payroll_handlers.payroll_view_command(update, ctx)
        assert "REVIEW" in update.effective_message.reply_text.call_args[0][0]


class TestHQInspection:
    async def test_runs_list_and_gate(self, stack):
        service = stack["service"]
        service.create_run("2026-09", "hq1", site_id="site-a")
        ctx = make_context(make_auth(), service)
        update = make_update("hq1", "/payroll_runs")
        await payroll_handlers.payroll_runs_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "2026-09" in text and "draft" in text

    async def test_runs_list_denied_for_pm(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth("project_manager"), service)
        update = make_update("pm1", "/payroll_runs")
        await payroll_handlers.payroll_runs_command(update, ctx)
        assert "HQ rights" in \
            update.effective_message.reply_text.call_args[0][0]


class TestExportHonesty:
    async def test_export_states_deferral(self, stack):
        service = stack["service"]
        ctx = make_context(make_auth(), service)
        await payroll_handlers.payroll_build_command(
            make_update("hq1", "/payroll_build 2026-09"), ctx)
        await payroll_handlers.payroll_add_command(
            make_update("hq1", "/payroll_add 2026-09 u1"), ctx)
        update = make_update("hq1", "/payroll_export 2026-09")
        await payroll_handlers.payroll_export_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "locked" in text and "deferred" in text
        assert "PDF in 021b" not in text


class TestAuditTrail:
    async def test_payroll_actions_logged(self, stack):
        service, events = stack["service"], stack["events"]
        ctx = make_context(make_auth(), service, events)
        await payroll_handlers.payroll_build_command(
            make_update("hq1", "/payroll_build 2026-09"), ctx)
        await payroll_handlers.payroll_add_command(
            make_update("hq1", "/payroll_add 2026-09 u1"), ctx)
        await payroll_handlers.payroll_export_command(
            make_update("hq1", "/payroll_export 2026-09"), ctx)
        update = make_update("hq1", "/payroll_adjust 2026-09 u1 -100 fix")
        await payroll_handlers.payroll_adjust_command(update, ctx)
        actions = [e.action for e in events.get_recent(limit=20)]
        for expected in ("payroll.run_created", "payroll.line_added",
                         "payroll.run_exported", "payroll.adjustment"):
            assert expected in actions
