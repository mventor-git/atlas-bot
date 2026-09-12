"""Phase 1 tenancy/security matrix: memberships are the ONLY authority.

Real AuthorizationService + real repositories over temp SQLite; Telegram
is mocked. Every test here must FAIL if a legacy/site-default vulnerability
returns.
"""

import tempfile
from datetime import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.auth import capabilities as caps
from app.bot import site_session
from app.bot.handlers import cases as case_handlers
from app.bot.handlers import hr as hr_handlers
from app.bot.handlers import payroll as payroll_handlers
from app.database.manager import DatabaseManager
from app.models.database import User
from app.repositories.attendance_repository import AttendanceRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.hr_repository import HRRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.money_repository import MoneyRepository
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.services.attendance_service import AttendanceService
from app.services.authorization_service import AuthorizationService
from app.services.case_service import CaseService
from app.services.hr_service import HRService
from app.services.payroll_service import PayrollService


# --- fixtures ---------------------------------------------------------------

class Stack:
    """Everything wired like main.py, minus Telegram."""

    def __init__(self, tmp: str):
        self.db = DatabaseManager(str(Path(tmp) / "t.db"))
        self.users = UserRepository(self.db)
        self.members = MembershipRepository(self.db)
        self.auth = AuthorizationService(
            user_repo=self.users, super_admin_chat_id="1",
            admin_chat_ids=[], membership_repo=self.members)
        self.cases = CaseService(CaseRepository(self.db))
        from app.repositories.discipline_repository import DisciplineRepository
        from app.services.discipline_service import DisciplineService
        self.disc = DisciplineService(DisciplineRepository(self.db))
        self.pay = PayrollService(PayrollRepository(self.db), self.users,
                                  membership_repo=self.members)
        self.att = AttendanceService(AttendanceRepository(self.db))
        self.hr = HRService(HRRepository(self.db), MoneyRepository(self.db))

    def make_user(self, chat_id, role="normal_user", site=None):
        self.users.upsert(User(chat_id=chat_id, role=role, site_id=site or "default"))

    def close(self):
        self.db.close_all()


@pytest.fixture
def stack():
    with tempfile.TemporaryDirectory() as tmp:
        s = Stack(tmp)
        yield s
        s.close()


def _msg(text="", user_id=10):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.reply_text = AsyncMock()
    update.effective_message = msg
    update.message = msg
    update.callback_query = None
    return update


def _ctx(stack, user_data=None):
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot_data = {
        "authorization_service": stack.auth,
        "case_service": stack.cases,
        "discipline_service": stack.disc,
        "payroll_service": stack.pay,
        "hr_service": stack.hr,
        "app_config": SimpleNamespace(sites=[{"id": "a"}, {"id": "b"}]),
    }
    ctx.user_data = {} if user_data is None else user_data
    ctx.args = []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


# --- capability semantics --------------------------------------------------

class TestCapabilitySemantics:
    def test_unknown_capability_denied(self, stack: Stack):
        stack.make_user("2")
        stack.members.grant("2", "a", ["review_grievance"])
        assert stack.auth.has_capability("2", "not_a_cap", "a") is False

    def test_pending_denied_even_with_row(self, stack: Stack):
        stack.make_user("3", role="pending")
        stack.members.grant("3", "a", ["check_in"])
        assert stack.auth.has_capability("3", "check_in", "a") is False

    def test_empty_grants_use_role_defaults(self, stack: Stack):
        stack.make_user("4", role="hr")
        stack.members.grant("4", "a", [])
        assert stack.auth.has_capability("4", "manage_payroll", "a") is True
        assert stack.auth.has_capability("4", "manage_payroll", "b") is False

    def test_explicit_grants_override_role(self, stack: Stack):
        stack.make_user("5", role="hr")
        stack.members.grant("5", "a", ["check_in"])
        assert stack.auth.has_capability("5", "check_in", "a") is True
        assert stack.auth.has_capability("5", "manage_payroll", "a") is False

    def test_suspend_site_a_keeps_site_b(self, stack: Stack):
        stack.make_user("6", role="project_manager")
        stack.members.grant("6", "a", [])
        stack.members.grant("6", "b", [])
        assert stack.auth.has_capability("6", "manage_attendance", "a") is True
        stack.members.suspend("6", "a")
        assert stack.auth.has_capability("6", "manage_attendance", "a") is False
        assert stack.auth.has_capability("6", "manage_attendance", "b") is True

    def test_revoke_blocks_then_regrant_works(self, stack: Stack):
        stack.make_user("7", role="normal_user")
        stack.members.grant("7", "a", [])
        assert stack.auth.has_capability("7", "create_daily_report", "a") is True
        assert stack.members.revoke("7", "a") is True
        assert stack.auth.has_capability("7", "create_daily_report", "a") is False
        stack.members.grant("7", "a", [])
        assert stack.auth.has_capability("7", "create_daily_report", "a") is True

    def test_role_change_ignored_where_grants_exist(self, stack: Stack):
        stack.make_user("8", role="viewer")
        stack.members.grant("8", "a", ["manage_attendance"])
        stack.users.set_role("8", "hr")
        assert stack.auth.has_capability("8", "manage_attendance", "a") is True
        assert stack.auth.has_capability("8", "view_hq_reports", "a") is False

    def test_legacy_users_site_id_is_not_authority(self, stack: Stack):
        # users.site_id says 'a' but membership exists only at 'b'
        stack.make_user("9", role="normal_user", site="a")
        stack.members.grant("9", "b", [])
        assert stack.auth.has_capability("9", "create_daily_report", "a") is False
        assert stack.auth.has_capability("9", "create_daily_report", "b") is True
        assert stack.auth.sites_for_user("9") == ["b"]
        assert stack.auth.resolve_active_site("9", None) == "b"

    def test_approve_grants_origin_membership(self, stack: Stack, monkeypatch):
        monkeypatch.setenv("SITE_ID", "a")
        from app.database import driver
        monkeypatch.setattr(driver, "site_id", lambda: "a")
        stack.make_user("10", role="pending")
        stack.auth.approve_user("10", "1")
        m = stack.members.find("10", "a")
        assert m is not None and m.status == "active"
        # legacy column value irrelevant: no membership row at 'b'
        assert stack.members.find("10", "b") is None


# --- cross-domain isolation at handler level (real auth) -------------------

class TestHandlerIsolation:
    async def test_reviewer_site_a_cannot_touch_site_b_case(self, stack: Stack):
        stack.make_user("11", role="hr", site="a")
        stack.members.grant("11", "a", [])
        victim = stack.cases.file("12", "grievance", "not yours", site_id="b")
        ctx = _ctx(stack, {"active_site": "a"})
        upd = MagicMock(spec=Update)
        upd.effective_user = MagicMock()
        upd.effective_user.id = 11
        query = MagicMock()
        query.data = f"case_review:{victim.id}"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        upd.callback_query = query
        upd.effective_message = upd.message = MagicMock(spec=Message)
        upd.message.reply_text = AsyncMock()
        await case_handlers.handle_case_callback(upd, ctx)
        query.edit_message_text.assert_called_once_with(
            "Review needs a reviewer account.")
        assert stack.cases.get(victim.id, site_id="b").status == "filed"

    async def test_queue_scoped_to_active_site(self, stack: Stack):
        stack.make_user("13", role="hr", site="a")
        stack.members.grant("13", "a", [])
        stack.cases.file("14", "complaint", "site B item", site_id="b")
        ctx = _ctx(stack)
        upd = _msg("/cases", user_id=13)
        await case_handlers.cases_command(upd, ctx)
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "empty" in text.lower()

    async def test_filing_pins_active_site(self, stack: Stack):
        stack.make_user("15", role="normal_user", site="a")
        stack.members.grant("15", "a", [])
        ctx = _ctx(stack)
        upd = _msg("/grievance", user_id=15)
        await case_handlers.grievance_command(upd, ctx)
        assert ctx.user_data["case_flow"]["site"] == "a"
        summary = _msg("cold showers", user_id=15)
        await case_handlers.handle_case_summary(summary, ctx)
        case = stack.cases.list_mine("15", site_id="a")
        assert len(case) == 1 and case[0].site_id == "a"
        assert stack.cases.list_mine("15", site_id="b") == []

    async def test_mypay_only_own_sites_own_lines(self, stack: Stack):
        stack.make_user("16", role="normal_user", site="a")
        stack.members.grant("16", "a", [])
        stack.users.set_salary("16", 10000)
        run = stack.pay.create_run("2026-09", "1", site_id="a")
        stack.pay.add_line(run.id, "16", 10000.0, site_id="a")
        other = stack.pay.create_run("2026-09", "1", site_id="b")
        stack.make_user("99", role="normal_user", site="b")
        stack.members.grant("99", "b", [])
        stack.users.set_salary("99", 7000)
        stack.pay.add_line(other.id, "99", 7000.0, site_id="b")
        ctx = _ctx(stack)
        upd = _msg("/mypay 2026-09", user_id=16)
        await payroll_handlers.mypay_command(upd, ctx)
        text = upd.effective_message.reply_text.call_args[0][0]
        assert "10000" in text and "7000" not in text

    async def test_discipline_review_cross_site_denied(self, stack: Stack):
        from app.bot.handlers import discipline as disc_handlers
        stack.make_user("40", role="hr", site="a")
        stack.members.grant("40", "a", [])
        service = stack.disc
        created = service.file("41", "42", "elsewhere", site_id="b")
        ctx = _ctx(stack, {"active_site": "a"})
        upd = MagicMock(spec=Update)
        upd.effective_user = MagicMock()
        upd.effective_user.id = 40
        q = MagicMock()
        q.data = f"disc_review:{created.id}"
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        upd.callback_query = q
        upd.message = upd.effective_message = MagicMock(spec=Message)
        upd.message.reply_text = AsyncMock()
        await disc_handlers.handle_discipline_callback(upd, ctx)
        assert "HQ-only" in q.edit_message_text.call_args[0][0]
        assert service.get(created.id, site_id="b").status == "filed"

    async def test_officer_denied_at_nonmember_site(self, stack: Stack):
        stack.make_user("17", role="project_manager", site="b")
        stack.members.grant("17", "b", [])
        ctx = _ctx(stack, {"active_site": "b"})
        upd = _msg("/payroll_build 2026-09", user_id=17)
        # site b: PM role default lacks manage_payroll -> denied even active
        await payroll_handlers.payroll_build_command(upd, ctx)
        denied = upd.effective_message.reply_text.call_args[0][0]
        assert "HQ" in denied or "rights" in denied.lower()
        assert stack.pay.get_run("2026-09", site_id="b") is None

    async def test_delegate_requires_decide_capability_at_site(self, stack: Stack, monkeypatch):
        from app.database import driver
        monkeypatch.setattr(driver, "site_id", lambda: "a")
        stack.make_user("18", role="hr", site="b")   # hr elsewhere, NOT at a
        stack.members.grant("18", "b", [])
        stack.members.grant("21", "a", [])
        stack.users.upsert(User(chat_id="21", role="admin", site_id="a"))
        req = stack.hr.request_advance("21", "boss", 500.0, "medical",
                                       site_id="a")
        ctx = _ctx(stack, {"hr_delegate_id": req.id, "hr_delegate_site": "a"})
        upd = _msg("18", user_id=21)
        await hr_handlers.handle_delegate_target(upd, ctx)
        assert "decision rights" in upd.message.reply_text.call_args[0][0]

    async def test_hr_notify_targets_memberships_only(self, stack: Stack, monkeypatch):
        from app.database import driver
        monkeypatch.setattr(driver, "site_id", lambda: "a")
        stack.make_user("1", role="superadmin")
        stack.members.grant("1", "a", [])
        stack.make_user("22", role="normal_user", site="a")   # legacy col says a
        stack.members.grant("23", "a", [])                     # true member
        stack.make_user("23", role="normal_user", site="b")
        hr = _msg("", user_id=1)                               # superadmin
        q = MagicMock()
        q.data = "hr_notify:a"
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        hr.callback_query = q
        ctx = _ctx(stack)
        from app.bot.handlers.hr import handle_board_callback
        ok = await handle_board_callback(hr, ctx, "hr_notify",
                                        ["hr_notify", "a"])
        assert ok is True
        sent = {c.kwargs["chat_id"] for c in ctx.bot.send_message.call_args_list}
        assert 23 in sent and 1 in sent      # memberships only
        assert 22 not in sent                # legacy users.site_id NOT authority
        assert "Queued 2 reminder" in q.edit_message_text.call_args[0][0]

    async def test_board_rejects_unknown_site_callback(self, stack: Stack, monkeypatch):
        from app.database import driver
        monkeypatch.setattr(driver, "site_id", lambda: "a")
        stack.make_user("1", role="superadmin")
        stack.members.grant("1", "a", [])
        hr = _msg("", user_id=1)
        q = MagicMock()
        q.data = "hr_notify:evil-site"
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        hr.callback_query = q
        ctx = _ctx(stack)
        from app.bot.handlers.hr import handle_board_callback
        await handle_board_callback(hr, ctx, "hr_notify", ["hr_notify", "evil-site"])
        assert q.edit_message_text.call_args[0][0] == "Unknown site."


# --- chat_ids_for_site (notification targeting) -----------------------------

class TestSiteRoster:
    def test_roster_filters_by_capability(self, stack: Stack):
        stack.make_user("30", role="normal_user")
        stack.members.grant("30", "a", [])
        stack.make_user("31", role="viewer")
        stack.members.grant("31", "a", [])
        stack.make_user("32", role="normal_user")
        stack.members.grant("32", "b", [])
        roster = stack.auth.chat_ids_for_site("a", "create_daily_report")
        assert roster == ["30"]                    # viewer lacks cap, b excluded
        assert "32" in stack.auth.chat_ids_for_site("b")

    def test_suspended_drops_immediately(self, stack: Stack):
        stack.make_user("33", role="normal_user")
        stack.members.grant("33", "a", [])
        assert "33" in stack.auth.chat_ids_for_site("a")
        stack.members.suspend("33", "a")
        assert "33" not in stack.auth.chat_ids_for_site("a")
