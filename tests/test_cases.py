"""Case engine tests (ticket-014): filing, review chain, appeal, isolation."""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.case import SUBMIT_CAPABILITY, CaseStatus, CaseType
from app.repositories.case_repository import CaseRepository
from app.services.case_service import CaseService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "c.db"))
        yield CaseService(CaseRepository(manager))
        manager.close_all()


class TestFiling:
    @pytest.mark.parametrize("kind", [
        CaseType.GRIEVANCE, CaseType.COMPLAINT,
        CaseType.SUGGESTION, CaseType.RESIGNATION,
    ])
    def test_all_types_file(self, service: CaseService, kind: str):
        case = service.file("u1", kind, "something happened", site_id="site-a")
        assert case.id is not None
        assert case.status == CaseStatus.FILED
        assert kind in SUBMIT_CAPABILITY  # handler gate exists per type

    def test_unknown_type_rejected(self, service: CaseService):
        with pytest.raises(DatabaseError):
            service.file("u1", "promotion", "want raise", site_id="site-a")

    def test_blank_summary_rejected(self, service: CaseService):
        with pytest.raises(DatabaseError):
            service.file("u1", CaseType.GRIEVANCE, "   ", site_id="site-a")


class TestChain:
    def _reviewed(self, service, **kwargs):
        kwargs.setdefault("site_id", "site-a")
        case = service.file("u1", CaseType.GRIEVANCE, "cold showers", **kwargs)
        return service.review(case.id, "rev1", site_id="site-a")

    def test_review_flow(self, service: CaseService):
        case = self._reviewed(service)
        assert case.status == CaseStatus.UNDER_REVIEW
        assert case.reviewed_by == "rev1"

    def test_resolve_needs_review_first(self, service: CaseService):
        case = service.file("u1", CaseType.COMPLAINT, "food", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.resolve(case.id, "rev1", "fixed", site_id="site-a")

    def test_resolve_needs_note(self, service: CaseService):
        case = self._reviewed(service)
        with pytest.raises(DatabaseError):
            service.resolve(case.id, "rev1", "  ", site_id="site-a")

    def test_resolve_flow(self, service: CaseService):
        case = self._reviewed(service)
        resolved = service.resolve(case.id, "rev1", "heaters ordered",
                                   site_id="site-a")
        assert resolved.status == CaseStatus.RESOLVED
        assert resolved.resolution_note == "heaters ordered"

    def test_double_review_refused(self, service: CaseService):
        case = self._reviewed(service)
        with pytest.raises(DatabaseError):
            service.review(case.id, "rev2", site_id="site-a")


class TestAppeal:
    def _resolved(self, service):
        case = service.file("u1", CaseType.GRIEVANCE, "cold showers",
                            site_id="site-a")
        service.review(case.id, "rev1", site_id="site-a")
        return service.resolve(case.id, "rev1", "heaters ordered",
                               site_id="site-a")

    def test_appeal_flow(self, service: CaseService):
        case = self._resolved(service)
        appealed = service.appeal(case.id, "u1", "still cold", site_id="site-a")
        assert appealed.status == CaseStatus.APPEALED
        re_reviewed = service.review(case.id, "rev2", site_id="site-a")
        assert re_reviewed.status == CaseStatus.UNDER_REVIEW

    def test_appeal_only_from_resolved(self, service: CaseService):
        case = service.file("u1", CaseType.GRIEVANCE, "cold", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.appeal(case.id, "u1", "hurry", site_id="site-a")

    def test_appeal_only_by_filer(self, service: CaseService):
        case = self._resolved(service)
        with pytest.raises(DatabaseError):
            service.appeal(case.id, "u9", "me too", site_id="site-a")

    def test_appeal_needs_note(self, service: CaseService):
        case = self._resolved(service)
        with pytest.raises(DatabaseError):
            service.appeal(case.id, "u1", "  ", site_id="site-a")


class TestIsolation:
    def test_cross_site_denied(self, service: CaseService):
        case = service.file("u1", CaseType.SUGGESTION, "more tea",
                            site_id="site-a")
        with pytest.raises(DatabaseError):
            service.review(case.id, "rev1", site_id="site-b")

    def test_reporter_queue_scoped(self, service: CaseService):
        service.file("u1", CaseType.COMPLAINT, "a", site_id="site-a")
        service.file("u1", CaseType.COMPLAINT, "b", site_id="site-b")
        assert len(service._repo.for_reporter("u1", site_id="site-a")) == 1

    def test_open_queue_excludes_resolved(self, service: CaseService):
        open_case = service.file("u1", CaseType.GRIEVANCE, "x", site_id="site-a")
        done = service.file("u2", CaseType.GRIEVANCE, "y", site_id="site-a")
        service.review(done.id, "rev1", site_id="site-a")
        service.resolve(done.id, "rev1", "done", site_id="site-a")
        ids = [c.id for c in service._repo.open_cases(site_id="site-a")]
        assert open_case.id in ids and done.id not in ids
