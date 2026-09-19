"""Register module: onboarding lifecycle (no server, tmp SQLite)."""

import re
import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.hr import HRRequestStatus, HRRequestType
from app.repositories.audit_repository import AuditRepository
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.hr_repository import HRRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService
from app.services.authorization_service import AuthorizationService
from app.services.hr_service import HRService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def stack():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "r.db"))
        users = UserRepository(manager)
        members = MembershipRepository(manager)
        auth = AuthorizationService(user_repo=users, super_admin_chat_id="1",
                                   admin_chat_ids=[], membership_repo=members)
        service = HRService(HRRepository(manager))
        employees = EmployeeRepository(manager)
        audit = AuditService(manager)
        yield {"manager": manager, "auth": auth, "service": service,
               "employees": employees, "members": members, "audit": audit}
        manager.close_all()


def _approve(stack, chat="222", name="Sara Nasser", role="normal_user",
             site="main", admin="1"):
    svc, emp, auth = stack["service"], stack["employees"], stack["auth"]
    req = svc.request_registration(chat, name, site_id=site)
    return svc.approve_registration(req.id, admin, "Boss", role, site,
                                    emp, auth, site_id=site)


class TestRequestApprove:
    def test_request_approve_code_card(self, stack):
        req = stack["service"].request_registration("222", "Sara Nasser",
                                                    site_id="main")
        assert req.request_type == HRRequestType.REGISTER
        assert req.status == HRRequestStatus.PENDING
        out = stack["service"].approve_registration(
            req.id, "1", "Boss", "normal_user", "main",
            stack["employees"], stack["auth"], site_id="main")
        assert out.status == HRRequestStatus.APPROVED
        assert out.note.startswith("EMP-001")
        code, name = stack["employees"].resolve("222")
        assert (code, name) == ("EMP-001", "Sara Nasser")
        card = stack["employees"].display("222")
        assert "EMP-001 · Sara Nasser" in card
        assert "222" not in card
        assert stack["members"].find("222", "main") is not None
        assert stack["auth"].get_role("222") == "normal_user"

    def test_second_hire_gets_next_code(self, stack):
        _approve(stack, chat="222", name="Sara Nasser")
        out = _approve(stack, chat="333", name="Omar Ali")
        assert out.note.startswith("EMP-002")

    def test_dup_chat_request_rejected(self, stack):
        stack["service"].request_registration("222", "Sara Nasser", site_id="main")
        with pytest.raises(DatabaseError):
            stack["service"].request_registration("222", "Sara Nasser",
                                                  site_id="main")

    def test_dup_chat_approve_rejected(self, stack):
        _approve(stack, chat="222")
        req = stack["service"].request_registration("222", "Sara Nasser",
                                                    site_id="main")
        # Active chat cannot re-approve: directory guard wins.
        with pytest.raises(DatabaseError):
            stack["service"].approve_registration(
                req.id, "1", "Boss", "normal_user", "main",
                stack["employees"], stack["auth"], site_id="main")

    def test_dup_code_rejected_by_constraint(self, stack):
        stack["employees"].register("222", "EMP-009", "Sara Nasser")
        with pytest.raises(Exception):
            stack["employees"].register("333", "EMP-009", "Omar Ali")

    def test_self_approve_blocked(self, stack):
        svc = stack["service"]
        req = svc.request_registration("222", "Sara Nasser", site_id="main")
        with pytest.raises(DatabaseError):
            svc.approve_registration(req.id, "222", "Sara", "normal_user",
                                     "main", stack["employees"],
                                     stack["auth"], site_id="main")

    def test_approve_needs_role_and_site(self, stack):
        svc = stack["service"]
        req = svc.request_registration("222", "Sara Nasser", site_id="main")
        with pytest.raises(DatabaseError):
            svc.approve_registration(req.id, "1", "Boss", "nobody", "main",
                                     stack["employees"], stack["auth"],
                                     site_id="main")
        req2 = svc.request_registration("333", "Omar Ali", site_id="main")
        with pytest.raises(DatabaseError):
            svc.approve_registration(req2.id, "1", "Boss", "normal_user", "",
                                     stack["employees"], stack["auth"],
                                     site_id="main")

    def test_reject_needs_reason(self, stack):
        svc = stack["service"]
        req = svc.request_registration("222", "Sara Nasser", site_id="main")
        with pytest.raises(DatabaseError):
            svc.reject_registration(req.id, "1", "Boss", note="",
                                    site_id="main")
        out = svc.reject_registration(req.id, "1", "Boss", note="no headcount",
                                      site_id="main")
        assert out.status == HRRequestStatus.REJECTED


class TestUpdateDeactivate:
    def test_update_name_role_site(self, stack):
        _approve(stack, chat="222", name="Sara Nasser", role="viewer",
                 site="main")
        emp = stack["service"].update_employee(
            "222", stack["employees"], stack["auth"],
            full_name="Sara N. Hassan", role="normal_user",
            site_id="hq", changed_by="1")
        assert emp.employee_code == "EMP-001"  # code kept on edit
        assert emp.full_name == "Sara N. Hassan"
        assert stack["auth"].get_role("222") == "normal_user"
        assert stack["members"].find("222", "hq") is not None

    def test_deactivated_blocked_and_retired(self, stack):
        _approve(stack, chat="222")
        assert stack["service"].deactivate_employee(
            "222", stack["employees"], stack["auth"]) is True
        assert stack["employees"].resolve("222") == ("UNMAPPED", "limited")
        card = stack["employees"].display("222")
        assert "UNMAPPED · limited" in card
        assert "/register" in card
        # Capabilities blocked: no active membership rows remain.
        assert stack["members"].active_for_user("222") == []
        # Re-hire mints a NEW code; retired code survives only in audit.
        req = stack["service"].request_registration("222", "Sara Nasser",
                                                    site_id="main")
        out = stack["service"].approve_registration(
            req.id, "1", "Boss", "normal_user", "main",
            stack["employees"], stack["auth"], site_id="main")
        assert out.note.startswith("EMP-002")

    def test_deactivate_unknown_rejected(self, stack):
        with pytest.raises(DatabaseError):
            stack["service"].deactivate_employee("999", stack["employees"],
                                                 stack["auth"])

    def test_audit_trail_writes(self, stack):
        audit_repo = AuditRepository(stack["manager"])
        stack["audit"].log_event("222", "pending", "register_requested",
                                 details="request #1", site_id="main")
        stack["audit"].log_event("1", "superadmin", "register_approved",
                                 details="EMP-001", site_id="main")
        rows = audit_repo.get_by_action("register_requested", site_id="main")
        assert len(rows) == 1
        got = audit_repo.get_by_action("register_approved", site_id="main")
        assert got and got[0].details == "EMP-001"

    def test_handler_wiring(self):
        from pathlib import Path

        from app.bot.handlers.register import (
            get_registration_handlers,
            handle_approve_detail,
            handle_reject_reason,
            handle_register_name,
        )

        handlers = get_registration_handlers()
        assert len(handlers) == 6  # register/queue/update/deactivate/my + reg_* buttons
        assert callable(handle_register_name)
        assert callable(handle_approve_detail)
        assert callable(handle_reject_reason)
        router = Path("app/bot/handlers/report_create.py").read_text(
            encoding="utf-8")
        for state in ("awaiting_register_name", "awaiting_reg_approve_detail",
                      "awaiting_reg_reject_reason"):
            assert state in router
