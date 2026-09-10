"""HR chain engine tests (ticket-006): pure service logic, no Telegram."""

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from app.database.manager import DatabaseManager
from app.models.hr import HRRequestStatus, HRRequestType
from app.repositories.hr_repository import HRRepository
from app.services.hr_service import HRService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def service() -> Generator[HRService, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "hr.db"))
        yield HRService(HRRepository(manager))
        manager.close_all()


@pytest.fixture
def advance(service: HRService):
    return service.request_advance("u1", "User One", 1500, "medical", site_id="site-a")


@pytest.fixture
def transport(service: HRService):
    return service.request_transport(
        "u1", "User One", 200, "site visit", trip_date="2026-09-01",
        report_ref="2026-09-01", receipt_path="/tmp/r.jpg", site_id="site-a")


class TestRequest:
    def test_advance_defaults(self, advance):
        assert advance.id is not None
        assert advance.status == HRRequestStatus.PENDING
        assert advance.site_id == "site-a"
        assert advance.signatures == []

    def test_transport_fields(self, transport):
        assert transport.request_type == HRRequestType.TRANSPORT
        assert transport.trip_date == "2026-09-01"
        assert transport.receipt_path == "/tmp/r.jpg"

    def test_bad_amount_rejected(self, service: HRService):
        with pytest.raises(DatabaseError):
            service.request_advance("u1", "U", 0, "x")
        with pytest.raises(DatabaseError):
            service.request_advance("u1", "U", -5, "x")

    def test_empty_reason_rejected(self, service: HRService):
        with pytest.raises(DatabaseError):
            service.request_advance("u1", "U", 100, "  ")


class TestChain:
    def test_confirm_then_approve(self, service: HRService, advance):
        service.confirm_pm(advance.id, "pm1", "PM One", site_id="site-a")
        decided = service.decide_hr(advance.id, "hr1", "HR One", True,
                                    deduction_month="2026-10", site_id="site-a")
        assert decided.status == HRRequestStatus.APPROVED
        assert decided.deduction_month == "2026-10"
        assert [s["decision"] for s in decided.signatures] == ["confirmed", "approved"]

    def test_reject_with_note(self, service: HRService, transport):
        service.confirm_pm(transport.id, "pm1", "PM", site_id="site-a")
        decided = service.decide_hr(transport.id, "hr1", "HR", False,
                                    note="no budget", site_id="site-a")
        assert decided.status == HRRequestStatus.REJECTED
        assert decided.note == "no budget"

    def test_hr_cannot_decide_before_pm(self, service: HRService, advance):
        with pytest.raises(DatabaseError):
            service.decide_hr(advance.id, "hr1", "HR", True,
                              deduction_month="2026-10", site_id="site-a")

    def test_approve_advance_needs_month(self, service: HRService, advance):
        service.confirm_pm(advance.id, "pm1", "PM", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.decide_hr(advance.id, "hr1", "HR", True,
                              deduction_month="next", site_id="site-a")

    def test_double_confirm_rejected(self, service: HRService, advance):
        service.confirm_pm(advance.id, "pm1", "PM", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.confirm_pm(advance.id, "pm1", "PM", site_id="site-a")

    def test_delegate_once_then_hr_decides(self, service: HRService, advance):
        service.delegate_to_hr(advance.id, "pm1", "PM", "hr9", site_id="site-a")
        again = service._repo.get_by_id(advance.id, site_id="site-a")
        assert again.delegated is True
        assert again.assigned_to == "hr9"
        assert again.status == HRRequestStatus.PM_CONFIRMED
        decided = service.decide_hr(advance.id, "hr9", "HR Nine", True,
                                    deduction_month="2026-11", site_id="site-a")
        assert decided.status == HRRequestStatus.APPROVED

    def test_no_redelegation(self, service: HRService, advance):
        service.delegate_to_hr(advance.id, "pm1", "PM", "hr9", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.delegate_to_hr(advance.id, "hr9", "HR Nine", "hr8", site_id="site-a")

    def test_cross_site_invisible(self, service: HRService, advance):
        with pytest.raises(DatabaseError):
            service.confirm_pm(advance.id, "pm1", "PM", site_id="site-b")

    def test_pdf_only_when_approved(self, service: HRService, advance):
        with pytest.raises(DatabaseError):
            service.record_pdf(advance.id, "/tmp/x.pdf", site_id="site-a")
        service.confirm_pm(advance.id, "pm1", "PM", site_id="site-a")
        service.decide_hr(advance.id, "hr1", "HR", True,
                          deduction_month="2026-10", site_id="site-a")
        done = service.record_pdf(advance.id, "/tmp/x.pdf", site_id="site-a")
        assert done.pdf_path == "/tmp/x.pdf"


class TestVisibility:
    def test_requester_sees_own_only(self, service: HRService, advance):
        service.request_advance("u2", "User Two", 100, "x", site_id="site-a")
        assert len(service.visible_to("u1", "normal_user", site_id="site-a")) == 1

    def test_full_role_sees_queue(self, service: HRService, advance):
        assert len(service.visible_to("pm1", "project_manager", site_id="site-a")) == 1
        assert len(service.visible_to("hr1", "hr", site_id="site-a")) == 1

    def test_other_site_sees_nothing(self, service: HRService, advance):
        assert service.visible_to("pm1", "project_manager", site_id="site-b") == []

    def test_history_windows(self, service: HRService, advance):
        assert len(service.history_for_user("u1", "month", site_id="site-a",
                                            today="2026-09-08")) == 1
        assert service.history_for_user("u1", "month", site_id="site-a",
                                        today="2026-08-01") == []
        assert len(service.history_for_user("u1", "year", site_id="site-a",
                                            today="2026-09-08")) == 1
