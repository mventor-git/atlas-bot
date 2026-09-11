"""Leave/mission/overtime tests (ticket-011): chain reuse, validation, closure."""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.hr import HRRequestStatus, HRRequestType
from app.repositories.hr_repository import HRRepository
from app.repositories.money_repository import MoneyRepository
from app.services.hr_service import HRService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "lmo.db"))
        yield HRService(HRRepository(manager), MoneyRepository(manager))
        manager.close_all()


def _approve(service: HRService, req, site="site-a"):
    service.confirm_pm(req.id, "pm1", "PM", site_id=site)
    return service.decide_hr(req.id, "hr1", "HR", True, site_id=site)


class TestLeave:
    def test_full_chain(self, service: HRService):
        req = service.request_leave("u1", "U", "family", "2026-10-01", "2026-10-03",
                                    site_id="site-a")
        assert req.request_type == HRRequestType.LEAVE
        assert req.status == HRRequestStatus.PENDING
        _approve(service, req)
        status = service.financial_status(req.id, site_id="site-a")
        assert status["state"] == "closed"

    def test_end_before_start_rejected(self, service: HRService):
        with pytest.raises(DatabaseError):
            service.request_leave("u1", "U", "x", "2026-10-05", "2026-10-01",
                                  site_id="site-a")

    def test_bad_date_rejected(self, service: HRService):
        with pytest.raises(DatabaseError):
            service.request_leave("u1", "U", "x", "tomorrow", "2026-10-01",
                                  site_id="site-a")

    def test_empty_reason_rejected(self, service: HRService):
        with pytest.raises(DatabaseError):
            service.request_leave("u1", "U", "  ", "2026-10-01", "2026-10-01",
                                  site_id="site-a")

    def test_no_money_leg(self, service: HRService):
        req = service.request_leave("u1", "U", "x", "2026-10-01", "2026-10-01",
                                    site_id="site-a")
        _approve(service, req)
        with pytest.raises(DatabaseError):
            service.record_payout(req.id, 100, "2026-10-02", "fin1", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.record_deduction(req.id, 100, "2026-10", "pay1", site_id="site-a")


class TestMission:
    def test_full_chain(self, service: HRService):
        req = service.request_mission("u1", "U", "client visit",
                                      "2026-10-02", "2026-10-03", site_id="site-a")
        _approve(service, req)
        assert service.financial_status(req.id, site_id="site-a")["state"] == "closed"

    def test_single_day_default(self, service: HRService):
        req = service.request_mission("u1", "U", "visit", "2026-10-02", site_id="site-a")
        assert req.start_date == "2026-10-02"
        assert req.end_date == "2026-10-02"


class TestOvertime:
    def test_full_chain(self, service: HRService):
        req = service.request_overtime("u1", "U", "2026-10-02", 3, "deadline",
                                       site_id="site-a")
        assert req.hours == 3
        _approve(service, req)
        assert service.financial_status(req.id, site_id="site-a")["state"] == "closed"

    def test_bad_hours_rejected(self, service: HRService):
        with pytest.raises(DatabaseError):
            service.request_overtime("u1", "U", "2026-10-02", 0, site_id="site-a")
        with pytest.raises(DatabaseError):
            service.request_overtime("u1", "U", "2026-10-02", 25, site_id="site-a")
        with pytest.raises(DatabaseError):
            service.request_overtime("u1", "U", "2026-10-02", "lots", site_id="site-a")


class TestIsolation:
    def test_types_scoped(self, service: HRService):
        req = service.request_leave("u1", "U", "x", "2026-10-01", "2026-10-02",
                                    site_id="site-a")
        with pytest.raises(DatabaseError):
            service.confirm_pm(req.id, "pm1", "PM", site_id="site-b")
