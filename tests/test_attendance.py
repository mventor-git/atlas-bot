"""Attendance tests (ticket-013): geofence math + evidence workflow."""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.attendance import (
    AttendanceStatus,
    AttendanceVerdict,
    LocationVerdict,
)
from app.repositories.attendance_repository import AttendanceRepository
from app.services.attendance_service import AttendanceService
from app.services.geofence import evaluate, haversine_m, in_circle, in_polygon
from app.utils.exceptions import DatabaseError

CIRCLE = {"type": "circle", "lat": 30.0, "lon": 31.0, "radius_m": 100}
SQUARE = {"type": "polygon",
          "points": [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]}


class TestGeofence:
    def test_haversine_zero(self):
        assert haversine_m(30.0, 31.0, 30.0, 31.0) == 0

    def test_haversine_known(self):
        assert 110000 < haversine_m(30.0, 31.0, 31.0, 31.0) < 113000

    def test_circle_inside_outside_boundary(self):
        assert in_circle(30.0, 31.0, 30.0, 31.0, 100) is True
        assert in_circle(31.0, 31.0, 30.0, 31.0, 100) is False
        assert in_circle(30.0 + 100 / 111320.0, 31.0, 30.0, 31.0, 100) is True

    def test_polygon_inside_outside(self):
        assert in_polygon(0.5, 0.5, SQUARE["points"]) is True
        assert in_polygon(2.0, 2.0, SQUARE["points"]) is False

    def test_polygon_vertex_edge_inside(self):
        assert in_polygon(0.0, 0.0, SQUARE["points"]) is True
        assert in_polygon(0.5, 0.0, SQUARE["points"]) is True

    def test_evaluate_cases(self):
        assert evaluate(30.0, 31.0, 10, CIRCLE) == LocationVerdict.INSIDE
        assert evaluate(31.0, 31.0, 10, CIRCLE) == LocationVerdict.OUTSIDE
        assert evaluate(None, None, None, CIRCLE) == LocationVerdict.UNAVAILABLE
        assert evaluate(30.0, 31.0, 10, None) == LocationVerdict.ANOMALY
        assert evaluate(30.0, 31.0, 900, CIRCLE) == LocationVerdict.ANOMALY
        assert evaluate(30.0, 31.0, 10, {"type": "bogus"}) == LocationVerdict.ANOMALY
        assert evaluate(0.5, 0.5, None, SQUARE) == LocationVerdict.INSIDE


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "a.db"))
        yield AttendanceService(AttendanceRepository(manager))
        manager.close_all()


class TestIntake:
    def test_check_in_inside(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", 30.0, 31.0, 10,
                                 CIRCLE, site_id="site-a")
        assert event.id is not None
        assert event.location_verdict == LocationVerdict.INSIDE
        assert event.status == AttendanceStatus.SUBMITTED
        assert event.method == "self"

    def test_missing_gps_never_absent(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", site_id="site-a")
        assert event.location_verdict == LocationVerdict.UNAVAILABLE
        assert event.verdict is None
        assert event.status == AttendanceStatus.SUBMITTED

    def test_outside_recorded_not_judged(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", 31.0, 31.0, 10,
                                 CIRCLE, site_id="site-a")
        assert event.location_verdict == LocationVerdict.OUTSIDE
        assert event.status == AttendanceStatus.SUBMITTED

    def test_assisted_explicit(self, service: AttendanceService):
        event = service.assisted_check_in("u2", "u1", "2026-09-11", "broken phone",
                                          30.0, 31.0, 10, CIRCLE, site_id="site-a")
        assert event.method == "assisted"
        assert event.initiated_by == "u1"
        assert event.assisted_target_chat_id == "u2"
        assert event.status == AttendanceStatus.PENDING_VERIFICATION

    def test_assisted_needs_reason(self, service: AttendanceService):
        with pytest.raises(DatabaseError):
            service.assisted_check_in("u2", "u1", "2026-09-11", "  ", site_id="site-a")

    def test_duplicates_visible(self, service: AttendanceService):
        service.check_in("u1", "2026-09-11", site_id="site-a")
        service.check_in("u1", "2026-09-11", site_id="site-a")
        assert len(service._repo.for_user_day("u1", "2026-09-11", site_id="site-a")) == 2


class TestWorkflow:
    def _confirmed(self, service, **kwargs):
        kwargs.setdefault("site_id", "site-a")
        event = service.check_in("u1", "2026-09-11", **kwargs)
        return service.confirm(event.id, "conf1", site_id="site-a")

    def test_confirm_flow(self, service: AttendanceService):
        event = self._confirmed(service)
        assert event.status == AttendanceStatus.CONFIRMED
        assert event.confirmed_by == "conf1"

    def test_confirm_invalid_state(self, service: AttendanceService):
        event = self._confirmed(service)
        with pytest.raises(DatabaseError):
            service.confirm(event.id, "conf1", site_id="site-a")

    def test_dispute_needs_note(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.dispute(event.id, "conf1", "  ", site_id="site-a")

    def test_dispute_then_confirm(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", site_id="site-a")
        service.dispute(event.id, "conf1", "wrong person?", site_id="site-a")
        confirmed = service.confirm(event.id, "conf1", site_id="site-a")
        assert confirmed.status == AttendanceStatus.CONFIRMED

    def test_exception_flow(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", site_id="site-a")
        service.mark_exception(event.id, "conf1", "device dead", site_id="site-a")
        resolved = service.resolve(event.id, AttendanceVerdict.EXCUSED, "hr1",
                                   site_id="site-a")
        assert resolved.status == AttendanceStatus.RESOLVED
        assert resolved.verdict == AttendanceVerdict.EXCUSED

    def test_resolve_needs_confirmation_first(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.resolve(event.id, AttendanceVerdict.PRESENT, "hr1",
                            site_id="site-a")

    def test_bad_verdict_rejected(self, service: AttendanceService):
        event = self._confirmed(service)
        with pytest.raises(DatabaseError):
            service.resolve(event.id, "super-present", "hr1", site_id="site-a")

    def test_lateness_is_fact(self, service: AttendanceService):
        from datetime import time

        event = self._confirmed(service)
        stamped = service.record_lateness(event.id, time(8, 47), site_id="site-a")
        assert stamped.late_minutes == 47
        on_time = service.record_lateness(event.id, time(7, 59), site_id="site-a")
        assert on_time.late_minutes == 0

    def test_cross_site_denied(self, service: AttendanceService):
        event = service.check_in("u1", "2026-09-11", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.confirm(event.id, "conf1", site_id="site-b")
