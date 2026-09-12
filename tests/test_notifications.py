"""Notification outbox tests (ticket-031, Stage 4): durability, dedup,
retry, multi-site isolation, eligibility, calendar, workflow types."""

import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.notification_repository import NotificationRepository
from app.services.notification_outbox import (
    NotificationOutbox, classify_error, dedup_key)

TODAY = date.today().isoformat()
FRIDAY = "2026-09-11"


def make_outbox(tmp=None):
    if tmp is None:
        tmp = tempfile.mkdtemp()
    manager = DatabaseManager(str(Path(tmp) / "n.db"))
    outbox = NotificationOutbox(NotificationRepository(manager))
    outbox._manager = manager
    return outbox


def make_auth(members=None, roles=None):
    """members: {chat: [sites]}; roles: {chat: role} (default normal_user)."""
    from app.auth import capabilities as caps

    members = members or {}
    roles = roles or {}
    auth = MagicMock()
    auth.sites_for_user.side_effect = lambda c: list(members.get(str(c), []))
    auth.get_role.side_effect = lambda c: roles.get(str(c), "normal_user")
    auth.is_super_admin.side_effect = lambda c: roles.get(str(c)) == "superadmin"

    def _roster(site, cap=None):
        cands = sorted(c for c, ss in members.items() if site in ss)
        if cap is None:
            return cands
        return sorted(c for c in cands
                      if cap in caps.for_role(roles.get(c, "normal_user")))

    auth.chat_ids_for_site.side_effect = _roster
    return auth


class FakeSend:
    """Scriptable sender: fail modes per recipient ('ok', 'timeout', 'gone')."""

    def __init__(self, modes=None):
        self.modes = modes or {}
        self.sent = []

    async def __call__(self, to, text):
        mode = self.modes.get(str(to), "ok")
        if mode == "ok":
            self.sent.append((str(to), text))
            return None
        if mode == "timeout":
            raise TimeoutError("timed out")
        if mode == "gone":
            raise ValueError("Forbidden: bot was blocked by the user")
        raise RuntimeError("boom")


class TestDedup:
    def test_same_logical_enqueue_returns_one_row(self):
        box = make_outbox()
        a = box.enqueue("morning", "111", "a", date=TODAY)
        b = box.enqueue("morning", "111", "a", date=TODAY)
        assert a.id == b.id
        assert box._repo.pending_count() == 1
        box._manager.close_all()

    def test_dedup_key_deterministic(self):
        assert dedup_key("a", "d", "1", "t", "r", 0) == \
            dedup_key("a", "d", "1", "t", "r", 0)
        assert dedup_key("a", "d", "1", "t", "r", 0) != \
            dedup_key("a", "d", "1", "t", "r", 1)

    def test_unknown_type_rejected(self):
        box = make_outbox()
        with pytest.raises(ValueError):
            box.enqueue("nope", "111", "a", date=TODAY)
        box._manager.close_all()


class TestRestartMatrix:
    async def test_case_a_pending_survives_restart_sends_once(self, tmp_path):
        box = make_outbox(str(tmp_path))
        box.enqueue("morning", "111", "a", date=TODAY)
        # "restart": brand-new outbox object on the same database file
        from app.repositories.notification_repository import (
            NotificationRepository)
        box2 = NotificationOutbox(NotificationRepository(
            DatabaseManager(str(tmp_path / "n.db"))))
        sender = FakeSend()
        out = await box2.dispatch(sender)
        assert out["sent"] == 1 and sender.sent[0][0] == "111"
        box._manager.close_all()

    async def test_case_b_sent_never_resends(self, tmp_path):
        box = make_outbox(str(tmp_path))
        box.enqueue("morning", "111", "a", date=TODAY)
        sender = FakeSend()
        await box.dispatch(sender)
        from app.repositories.notification_repository import (
            NotificationRepository)
        box2 = NotificationOutbox(NotificationRepository(
            DatabaseManager(str(tmp_path / "n.db"))))
        out = await box2.dispatch(FakeSend())
        assert out["sent"] == 0
        box._manager.close_all()

    async def test_case_c_retry_then_terminal(self):
        box = make_outbox()
        row = box.enqueue("morning", "111", "a", date=TODAY)
        sender = FakeSend({"111": "timeout"})
        out = await box.dispatch(sender, now_iso="2026-09-12T09:00:00")
        assert out["failed"] == 1
        again = box._repo.get_by_dedup(row.dedup_key)
        assert again.status == "pending" and again.next_retry_at is not None
        # exhaust attempts: attempt counts grow per failure
        for i in range(5):
            await box.dispatch(sender, now_iso=f"2026-09-1{2+i}T09:00:00")
        final = box._repo.get_by_dedup(row.dedup_key)
        assert final.status == "failed"
        box._manager.close_all()

    async def test_case_c_permanent_fails_fast(self):
        box = make_outbox()
        row = box.enqueue("morning", "111", "a", date=TODAY)
        out = await box.dispatch(FakeSend({"111": "gone"}))
        assert out["failed"] == 1
        assert box._repo.get_by_dedup(row.dedup_key).status == "failed"
        box._manager.close_all()

    async def test_case_d_site_failure_isolated(self):
        box = make_outbox()
        box.enqueue("morning", "1", "a", date=TODAY)
        box.enqueue("morning", "2", "b", date=TODAY)
        out = await box.dispatch(FakeSend({"1": "gone"}))
        assert out["sent"] == 1 and out["failed"] == 1
        box._manager.close_all()

    async def test_case_e_double_dispatch_sends_once(self):
        box = make_outbox()
        box.enqueue("morning", "111", "a", date=TODAY)
        sender = FakeSend()
        first = await box.dispatch(sender)
        second = await box.dispatch(sender)
        assert (first["sent"], second["sent"]) == (1, 0)
        assert len(sender.sent) == 1
        box._manager.close_all()

    async def test_stuck_sending_reclaimed(self):
        box = make_outbox()
        row = box.enqueue("morning", "111", "a", date=TODAY)
        claimed = box._repo.claim(row.id, "2026-09-12T09:00:00")
        assert claimed is not None and claimed.status == "sending"
        # fresh tick inside the lease: not stealable
        assert box._repo.claim(row.id, "2026-09-12T09:01:00") is None
        # after the crash window: reclaimable and sendable
        out = await box.dispatch(FakeSend(), now_iso="2026-09-12T10:00:00")
        assert out["sent"] == 1
        box._manager.close_all()


class TestEligibility:
    async def test_revoked_member_skipped(self):
        box = make_outbox()
        box.enqueue("morning", "111", "a", date=TODAY)
        auth = make_auth(members={"222": ["a"]})  # 111 lost membership
        out = await box.dispatch(FakeSend(), auth=auth)
        assert out["skipped"] == 1 and out["sent"] == 0
        box._manager.close_all()

    async def test_pending_role_never_gets_private_notice(self):
        box = make_outbox()
        box.enqueue("case_update", "111", "a", date=TODAY)
        auth = make_auth(members={"111": ["a"]}, roles={"111": "pending"})
        out = await box.dispatch(FakeSend(), auth=auth)
        assert out["skipped"] == 1
        box._manager.close_all()

    def test_classify(self):
        assert classify_error(ValueError("Forbidden: blocked"))[1] is True
        assert classify_error(TimeoutError("x"))[1] is False
        assert classify_error(RuntimeError("boom"))[1] is False

    async def test_ack_and_cancel(self):
        box = make_outbox()
        row = box.enqueue("morning", "111", "a", date=TODAY,
                          reference="2026-09-12")
        assert box.ack(row.id) is True
        assert box.ack(row.id) is False  # already acked
        box.enqueue("morning", "222", "a", date=TODAY,
                    reference="2026-09-12")
        assert box.cancel_for("a", "2026-09-12") == 2
        assert box._repo.pending_count() == 0
        box._manager.close_all()


class TestTemplates:
    def test_all_types_build(self):
        from app.services import notification_templates as t

        for name in t.TYPES:
            text = t.build(name, site="a", date="2026-09-12", subject="5",
                           kind="in", verdict="ok", note="n", ref="1",
                           hours=2, period="2026-09", outcome="x",
                           reference="r")
            assert isinstance(text, str) and len(text) > 10


def _manager_config(sites):
    from app.models.config import AppConfig

    return AppConfig(**{
        "template": {"file": "t.ots", "tables_file": "t.ods"},
        "date": {"cell": "B7", "day_cell": "B5"},
        "table": {"start_row": 11, "columns": {}},
        "output": {"pdf_folder": "x", "docs_folder": "y"},
        "database": {"path": ":memory:"},
        "tables": {"contractor_sheet": "c", "zones_sheet": "z"},
        "lifecycle": {"auto_lock_hours": 24, "max_versions": 50,
                      "auto_finalize_hour": 0, "auto_finalize_minute": 0},
        "timezone": {"name": "Africa/Cairo"},
        "notification": {"enabled": True},
        "sites": sites,
    })


def _manager(tmp, sites, members, roles=None, services=None):
    from app.services.notification_manager import NotificationManager

    manager = DatabaseManager(str(Path(tmp) / "m.db"))
    app = SimpleNamespace(bot_data=dict(services or {}),
                          bot=MagicMock())
    app.bot.send_message = AsyncMock()
    auth = make_auth(members, roles)
    app.bot_data["authorization_service"] = auth
    cfg = _manager_config(sites)
    mgr = NotificationManager(app, manager, cfg)
    mgr._manager = manager
    return mgr, auth


class TestSchedulerSweep:
    async def test_friday_off_vs_working(self, tmp_path):
        mgr, _ = _manager(str(tmp_path),
                          [{"id": "a"}, {"id": "b", "weekend": []}],
                          {"10": ["a"], "20": ["b"]},
                          {"10": "normal_user", "20": "normal_user"})
        out = await mgr.run_cycle(FRIDAY, 9, 0)
        assert out["a"].get("skipped") == "weekend"
        assert out["a"].get("morning", 0) == 0
        assert out["b"].get("morning", 0) == 1
        sent_to = {c.kwargs["chat_id"] for c in
                   mgr._app.bot.send_message.call_args_list}
        assert sent_to == {20}
        mgr._manager.close_all()

    async def test_escalation_reaches_reviewers(self, tmp_path):
        mgr, _ = _manager(str(tmp_path), [{"id": "a"}],
                          {"10": ["a"], "90": ["a"]},
                          {"10": "normal_user", "90": "project_manager"})
        out = await mgr.run_cycle("2026-09-14", 19, 0)  # Monday past deadline
        assert out["a"].get("escalated", 0) == 1
        sent_to = {c.kwargs["chat_id"] for c in
                   mgr._app.bot.send_message.call_args_list}
        assert sent_to == {90}   # reviewer only; creator not re-paged
        mgr._manager.close_all()

    async def test_final_review_queued_for_final(self, tmp_path):
        from app.repositories.report_repository import ReportRepository

        mgr, _ = _manager(str(tmp_path), [{"id": "a"}],
                          {"10": ["a"], "90": ["a"]},
                          {"10": "normal_user", "90": "project_manager"})
        repo = ReportRepository(mgr._manager)
        repo.add(Report(date="2026-09-14", day="Mon",
                        status=ReportStatus.FINAL, telegram_user="10",
                        site_id="a",
                        items=[ReportItem(contractor="C", workers=2)]))
        out = await mgr.run_cycle("2026-09-14", 15, 0)
        assert out["a"].get("review_queued") is True
        # run_cycle drains immediately: reviewer got it, nothing left pending
        sent_to = {c.kwargs["chat_id"] for c in
                   mgr._app.bot.send_message.call_args_list}
        assert sent_to == {90}
        assert mgr.outbox.pending_count("a") == 0
        mgr._manager.close_all()


class TestWorkflowNotices:
    async def test_case_review_notice_durable(self, tmp_path):
        from app.bot.notify import notify

        manager = DatabaseManager(str(Path(tmp_path) / "w.db"))
        from app.repositories.notification_repository import (
            NotificationRepository)
        from app.services.notification_outbox import NotificationOutbox

        outbox = NotificationOutbox(NotificationRepository(manager))
        auth = make_auth({"111": ["a"]})
        bot = MagicMock()
        bot.send_message = AsyncMock()
        ctx = MagicMock()
        ctx.bot_data = {"notification_outbox": outbox,
                        "authorization_service": auth}
        ctx.bot = bot
        # failing sender first: row persists, then succeeds on retry
        bot.send_message.side_effect = [TimeoutError("t"), None]
        await notify(ctx, "case_update", "111", "a", reference="grievance:7",
                     date=TODAY, kind="grievance", ref=7,
                     verdict="under review", note="")
        row = outbox._repo.get_by_dedup("a|%s|111|case_update|grievance:7|0"
                                        % TODAY)
        assert row is not None and row.status == "pending"
        await outbox.dispatch(
            lambda to, text: bot.send_message(chat_id=int(to), text=text),
            auth=auth, now_iso="2026-09-13T09:00:00")
        assert bot.send_message.call_count == 2
        manager.close_all()


class TestAdminCommands:
    async def test_cycle_and_pending_gates(self, tmp_path):
        from app.bot.handlers import admin as admin_handlers

        mgr, _ = _manager(str(tmp_path), [{"id": "a"}], {"1": ["a"]},
                          {"1": "superadmin"})
        mgr._app.bot_data["notification_outbox"] = mgr.outbox
        mgr._app.bot_data["notification_manager"] = mgr

        def _upd(uid, text="/notify_cycle"):
            update = MagicMock()
            update.effective_user = MagicMock()
            update.effective_user.id = uid
            msg = MagicMock()
            msg.text = text
            msg.reply_text = AsyncMock()
            update.message = msg
            update.effective_message = msg
            return update

        def _ctx():
            ctx = MagicMock()
            ctx.bot_data = mgr._app.bot_data
            ctx.args = []
            return ctx

        await admin_handlers.notify_cycle_command(_upd(1), _ctx())
        await admin_handlers.notify_pending_command(_upd(1), _ctx())
        denied = _upd(111)
        await admin_handlers.notify_cycle_command(denied, _ctx())
        assert "Superadmin" in denied.message.reply_text.call_args[0][0]
        mgr._manager.close_all()
