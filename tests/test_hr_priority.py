"""Priority engine + employee directory checks (no server, tmp SQLite)."""

import re
import tempfile
from pathlib import Path

import pytest

from app.bot.handlers.hr import _render
from app.database.manager import DatabaseManager
from app.models.hr import HRRequest
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.hr_repository import HRRepository
from app.services.hr_priority import priority_for, sort_low_last
from app.services.hr_service import HRService


@pytest.fixture
def manager():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = DatabaseManager(str(Path(tmp) / "p.db"))
        yield mgr
        mgr.close_all()


@pytest.fixture
def service(manager):
    return HRService(HRRepository(manager))


@pytest.fixture
def employees(manager):
    return EmployeeRepository(manager)


def _transport(**kw):
    base = dict(requester_chat_id="123456789", requester_name="Tg Name",
                request_type="transport", amount=200, reason="site visit",
                trip_date="2026-09-01")
    base.update(kw)
    return HRRequest(**base)


class TestPriority:
    def test_transport_without_receipt_is_low(self):
        assert priority_for(_transport(receipt_path="")) == "low"

    def test_transport_complete_is_normal(self):
        doc = _transport(receipt_path="/x/r.jpg", report_ref="2026-09-01")
        assert priority_for(doc) == "normal"

    def test_reports_exempt(self):
        assert priority_for({"doc_type": "contractor"}) == "normal"
        assert priority_for({"doc_type": "labor_report"}) == "normal"
        assert priority_for({"report_type": "report"}) == "normal"

    def test_advance_junk_reason_is_low(self):
        doc = {"request_type": "advance", "reason": "n/a"}
        assert priority_for(doc) == "low"

    def test_claim_blank_text_is_low(self):
        assert priority_for({"kind": "correction", "text": "  "}) == "low"

    def test_grievance_blank_summary_is_low(self):
        assert priority_for({"case_type": "grievance", "summary": ""}) == "low"

    def test_sort_low_last_stable(self):
        low = _transport(id=1)
        normal = _transport(id=2, receipt_path="/x.jpg",
                            report_ref="2026-09-01")
        assert [r.id for r in sort_low_last([low, normal])] == [2, 1]

    def test_queue_sorts_low_last(self, service):
        service.request_transport("u1", "U", 100, "trip",
                                  trip_date="2026-09-01", site_id="s")
        service.request_transport("u2", "U", 100, "trip",
                                  trip_date="2026-09-01", report_ref="r",
                                  receipt_path="/x.jpg", site_id="s")
        rows = service.queue(site_id="s")
        assert [r.requester_chat_id for r in rows] == ["u2", "u1"]


class TestDirectory:
    def test_unknown_chat_unmapped(self, employees):
        assert employees.resolve("999888777") == ("UNMAPPED", "limited")
        card = employees.display("999888777")
        assert "UNMAPPED · limited" in card
        assert "999888777" not in card  # never raw chat ID

    def test_register_then_resolve(self, employees):
        employees.register("123456789", "EMP-014", "Sara Nasser")
        assert employees.resolve("123456789") == ("EMP-014", "Sara Nasser")

    def test_masked_card_has_zero_long_digit_runs(self, employees):
        employees.register("123456789", "EMP-014", "Sara Nasser")
        code, name = employees.resolve("123456789")
        card = _render(_transport(id=7, receipt_path="",
                                  requester_chat_id="123456789"),
                       f"{code} · {name}")
        assert "123456789" not in card  # raw chat ID never rendered
        assert "Tg Name" not in card  # telegram display name never shown
        assert "EMP-014 · Sara Nasser" in card
        assert re.findall(r"\d{8,}", card) == []  # zero long digit runs
        assert "Priority: LOW" in card  # badge present, still filed
