"""Payroll safety hardening tests (Stage 5A): no-policy integrity invariants.

Covers: UNIQUE(run_id, chat_id), salary history + line provenance,
cross-site subject check, delete/lock guard, real-month periods.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import payroll as payroll_handlers
from app.database.manager import DatabaseManager
from app.models.database import User
from app.models.payroll import PayrollRunStatus
from app.repositories.membership_repository import MembershipRepository
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.services.payroll_service import PayrollService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def setup():
    """Temp DB, membership-wired service, u1@site-a, u2@site-b, u3@both."""
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "p.db"))
        users = UserRepository(manager)
        members = MembershipRepository(manager)
        for chat_id, site in (("u1", "site-a"), ("u2", "site-b"),
                              ("u3", "site-a")):
            users.upsert(User(chat_id=chat_id, role="normal_user",
                              site_id=site))
            members.grant(chat_id, site)
        members.grant("u3", "site-b")
        users.set_salary("u1", 12000.0, set_by="hq1",
                           effective_from="2026-01-01")
        users.set_salary("u2", 8000.0, set_by="hq1",
                         effective_from="2026-01-01")
        users.set_salary("u3", 10000.0, set_by="hq1",
                         effective_from="2026-01-01")
        service = PayrollService(PayrollRepository(manager), users,
                                 membership_repo=members)
        yield service, users, members, manager
        manager.close_all()


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


def make_auth():
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = "hr"
    auth.resolve_active_site.side_effect = (
        lambda chat_id, session_site=None: session_site or "site-a")
    auth.sites_for_user.return_value = ["site-a"]
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role("hr"))
    return auth


class TestSalaryHistory:
    def test_set_creates_history_with_actor_and_time(self, setup):
        _, users, _, _ = setup
        rows = users.salary_history_for("u1")
        assert len(rows) == 1
        row = rows[0]
        assert row["amount"] == 12000.0
        assert row["set_by"] == "hq1"
        assert row["set_at"] and row["effective_from"]
        assert users.get_by_chat_id("u1").monthly_salary == 12000.0

    def test_second_set_appends_leaves_old_row(self, setup):
        _, users, _, _ = setup
        users.set_salary("u1", 13000.0, set_by="hq2", reason="raise")
        rows = users.salary_history_for("u1")
        assert [r["amount"] for r in rows] == [12000.0, 13000.0]
        assert rows[0]["set_by"] == "hq1"
        assert rows[1]["reason"] == "raise"

    def test_current_record_is_latest(self, setup):
        _, users, _, _ = setup
        users.set_salary("u1", 13000.0, set_by="hq2")
        current = users.current_salary_record("u1")
        assert current["amount"] == 13000.0
        assert current["set_by"] == "hq2"

    def test_csv_import_records_actor(self, setup):
        service, users, _, _ = setup
        report = service.import_salaries("u1,14000", set_by="hq9")
        assert report["updated"] == 1
        assert users.current_salary_record("u1")["set_by"] == "hq9"

    def test_legacy_baseline_backfill(self, setup):
        _, users, _, manager = setup
        manager.execute("DELETE FROM salary_history WHERE chat_id = 'u2'")
        manager.commit()
        assert users.current_salary_record("u2") is None
        manager._ensure_payroll_safety()
        baseline = users.current_salary_record("u2")
        assert baseline["amount"] == 8000.0
        assert baseline["set_by"] == "migration"
        assert baseline["reason"] == "legacy salary baseline"


class TestLineProvenance:
    def test_line_cites_salary_version(self, setup):
        service, users, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        assert line.salary_history_id == users.current_salary_record("u1")["id"]

    def test_old_line_keeps_provenance_after_raise(self, setup):
        service, users, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        old_ref, old_base = line.salary_history_id, line.base_pay
        users.set_salary("u1", 15000.0, set_by="hq1", reason="raise")
        kept = service.lines(run)[0]
        assert (kept.base_pay, kept.salary_history_id) == (old_base, old_ref)


class TestDuplicateLines:
    def test_second_add_refused(self, setup):
        service, _, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        with pytest.raises(DatabaseError, match="already has a line"):
            service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        assert len(service.lines(run)) == 1

    def test_db_constraint_rejects_direct_duplicate(self, setup):
        service, _, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        clone = service._repo.lines_for(run.id)[0]
        clone.id = None
        with pytest.raises(DatabaseError):
            service._repo.add_line(clone)
        assert line.id is not None

    def test_update_existing_line_still_works(self, setup):
        service, _, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        fixed = service.update_line(line.id, run.id, 12000.0, 0.0,
                                    1000.0, 0.0, site_id="site-a")
        assert fixed.net == 11000.0
        assert len(service.lines(run)) == 1

    async def test_handler_duplicate_returns_safe_error(self, setup):
        service, _, _, _ = setup
        ctx = make_context(make_auth(), service)
        await payroll_handlers.payroll_build_command(
            make_update("hq1", "/payroll_build 2026-09"), ctx)
        await payroll_handlers.payroll_add_command(
            make_update("hq1", "/payroll_add 2026-09 u1"), ctx)
        update = make_update("hq1", "/payroll_add 2026-09 u1")
        await payroll_handlers.payroll_add_command(update, ctx)
        reply = update.effective_message.reply_text.call_args[0][0]
        assert "already has a line" in reply


class TestSubjectSite:
    def test_site_b_employee_refused_in_site_a(self, setup):
        service, _, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError, match="no active membership"):
            service.add_line(run.id, "u2", 8000.0, site_id="site-a")

    def test_multi_site_employee_allowed_either_site(self, setup):
        service, _, _, _ = setup
        run_a = service.create_run("2026-09", "hq1", site_id="site-a")
        run_b = service.create_run("2026-09", "hq1", site_id="site-b")
        assert service.add_line(run_a.id, "u3", 10000.0,
                                site_id="site-a").net == 10000.0
        assert service.add_line(run_b.id, "u3", 10000.0,
                                site_id="site-b").net == 10000.0

    def test_suspended_membership_denied(self, setup):
        service, _, members, _ = setup
        assert members.suspend("u1", "site-a") is True
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError, match="no active membership"):
            service.add_line(run.id, "u1", 12000.0, site_id="site-a")

    def test_revoked_membership_denied(self, setup):
        service, _, members, _ = setup
        assert members.revoke("u1", "site-a") is True
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError, match="no active membership"):
            service.add_line(run.id, "u1", 12000.0, site_id="site-a")

    def test_legacy_mode_keeps_site_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = DatabaseManager(str(Path(tmp) / "p.db"))
            try:
                users = UserRepository(manager)
                users.upsert(User(chat_id="u1", role="normal_user",
                                  site_id="site-a"))
                users.set_salary("u1", 12000.0, effective_from="2026-01-01")
                legacy = PayrollService(PayrollRepository(manager), users)
                run = legacy.create_run("2026-09", "hq1", site_id="site-a")
                assert legacy.add_line(run.id, "u1", 12000.0,
                                       site_id="site-a").net == 12000.0
                other = legacy.create_run("2026-10", "hq1", site_id="site-b")
                with pytest.raises(DatabaseError):
                    legacy.add_line(other.id, "u1", 12000.0,
                                    site_id="site-b")
            finally:
                manager.close_all()


class TestDeleteLock:
    def test_delete_draft_allowed(self, setup):
        service, _, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        assert service._repo.delete(run.id, site_id="site-a") is True
        assert service.get_run("2026-09", site_id="site-a") is None

    def test_delete_exported_refused(self, setup):
        service, _, _, _ = setup
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        service.mark_exported(run.id, site_id="site-a")
        with pytest.raises(ValueError, match="immutable"):
            service._repo.delete(run.id, site_id="site-a")
        locked = service.get_run("2026-09", site_id="site-a")
        assert locked.status == PayrollRunStatus.EXPORTED
        assert len(service.lines(locked)) == 1

    def test_delete_missing_returns_false(self, setup):
        service, _, _, _ = setup
        assert service._repo.delete(999999, site_id="site-a") is False


class TestPeriodValidation:
    @pytest.mark.parametrize("period", ["2026-01", "2026-12"])
    def test_valid_months_accepted(self, setup, period):
        service, _, _, _ = setup
        assert service.create_run(period, "hq1", site_id="site-a").period == period

    @pytest.mark.parametrize("period", ["2026-00", "2026-13", "2026-1",
                                        "sep-2026", "2026/09", ""])
    def test_invalid_periods_rejected(self, setup, period):
        service, _, _, _ = setup
        with pytest.raises(DatabaseError):
            service.create_run(period, "hq1", site_id="site-a")
