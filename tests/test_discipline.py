"""Discipline engine tests (ticket-017): filing, SoD, appeal, isolation."""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.discipline import DisciplineStatus
from app.repositories.discipline_repository import DisciplineRepository
from app.services.discipline_service import DisciplineService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "d.db"))
        yield DisciplineService(DisciplineRepository(manager))
        manager.close_all()


class TestFiling:
    def test_file(self, service: DisciplineService):
        case = service.file("u1", "hq1", "late three times", site_id="site-a")
        assert case.id is not None
        assert case.status == DisciplineStatus.FILED

    def test_self_filing_refused(self, service: DisciplineService):
        with pytest.raises(DatabaseError):
            service.file("hq1", "hq1", "myself", site_id="site-a")

    def test_blank_summary_rejected(self, service: DisciplineService):
        with pytest.raises(DatabaseError):
            service.file("u1", "hq1", "   ", site_id="site-a")


class TestChain:
    def _reviewed(self, service):
        case = service.file("u1", "hq1", "late", site_id="site-a")
        return service.review(case.id, "hq2", site_id="site-a")

    def test_decide_flow(self, service: DisciplineService):
        case = self._reviewed(service)
        decided = service.decide(case.id, "hq2", "written warning",
                                 "third late arrival", site_id="site-a")
        assert decided.status == DisciplineStatus.DECIDED
        assert decided.decision == "written warning"

    def test_filer_cannot_decide(self, service: DisciplineService):
        case = self._reviewed(service)
        with pytest.raises(DatabaseError):
            service.decide(case.id, "hq1", "warning", "note", site_id="site-a")

    def test_decide_needs_review_first(self, service: DisciplineService):
        case = service.file("u1", "hq1", "late", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.decide(case.id, "hq2", "warning", "note", site_id="site-a")

    def test_decide_needs_outcome_and_note(self, service: DisciplineService):
        case = self._reviewed(service)
        with pytest.raises(DatabaseError):
            service.decide(case.id, "hq2", "  ", "note", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.decide(case.id, "hq2", "warning", "  ", site_id="site-a")


class TestAppeal:
    def _decided(self, service):
        case = service.file("u1", "hq1", "late", site_id="site-a")
        service.review(case.id, "hq2", site_id="site-a")
        return service.decide(case.id, "hq2", "warning", "note", site_id="site-a")

    def test_appeal_flow(self, service: DisciplineService):
        case = self._decided(service)
        appealed = service.appeal(case.id, "u1", "bus broke down",
                                  site_id="site-a")
        assert appealed.status == DisciplineStatus.APPEALED
        assert service.review(case.id, "hq3", site_id="site-a").status == \
            DisciplineStatus.UNDER_REVIEW

    def test_appeal_only_by_subject(self, service: DisciplineService):
        case = self._decided(service)
        with pytest.raises(DatabaseError):
            service.appeal(case.id, "u9", "me too", site_id="site-a")

    def test_appeal_only_from_decided(self, service: DisciplineService):
        case = service.file("u1", "hq1", "late", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.appeal(case.id, "u1", "hurry", site_id="site-a")


class TestIsolation:
    def test_cross_site_denied(self, service: DisciplineService):
        case = service.file("u1", "hq1", "late", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.review(case.id, "hq2", site_id="site-b")

    def test_subject_queue_scoped(self, service: DisciplineService):
        service.file("u1", "hq1", "a", site_id="site-a")
        service.file("u1", "hq1", "b", site_id="site-b")
        assert len(service._repo.for_subject("u1", site_id="site-a")) == 1

    def test_open_queue_excludes_decided(self, service: DisciplineService):
        open_case = service.file("u1", "hq1", "x", site_id="site-a")
        done = service.file("u2", "hq1", "y", site_id="site-a")
        service.review(done.id, "hq2", site_id="site-a")
        service.decide(done.id, "hq2", "warning", "done", site_id="site-a")
        ids = [c.id for c in service._repo.open_cases(site_id="site-a")]
        assert open_case.id in ids and done.id not in ids
