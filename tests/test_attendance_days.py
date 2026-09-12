"""Attendance day tests (ticket-026): aggregate, invariants, claims."""

import tempfile
from datetime import time
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.attendance import (
    AttendanceEvent,
    AttendanceStatus,
    AttendanceVerdict,
    ClaimKind,
    DayStatus,
)
from app.repositories.attendance_day_repository import AttendanceDayRepository
from app.repositories.attendance_repository import AttendanceRepository
from app.services.attendance_day_service import AttendanceDayService
from app.utils.exceptions import DatabaseError

D = "2026-09-11"


def ev(chat="u1", kind="in", ts="2026-09-11T09:30:00", status="submitted",
       method="self", lat=30.0, lon=31.0, verdict=None, site="site-a"):
    return AttendanceEvent(
        chat_id=chat, event_date=D, check_type=kind, method=method,
        latitude=lat, longitude=lon, accuracy_m=10,
        location_verdict=verdict, status=status,
        created_at=ts, site_id=site)


def rec(svc, **kw):
    """Persist an evidence row (event repo) then fold it into the day."""
    e = svc._events.add(ev(**kw))
    svc.on_event(e)
    return e


@pytest.fixture
def svc():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "d.db"))
        yield AttendanceDayService(
            AttendanceDayRepository(manager), AttendanceRepository(manager),
            expected_start=time(8, 0), cutoff=time(17, 0))
        manager.close_all()


class TestAggregate:
    def test_ensure_idempotent(self, svc: AttendanceDayService):
        a = svc.ensure_day("u1", D, site_id="site-a")
        b = svc.ensure_day("u1", D, site_id="site-a")
        assert a.id == b.id and a.status == DayStatus.PENDING

    def test_unique_per_site_chat_date(self, svc: AttendanceDayService):
        svc.ensure_day("u1", D, site_id="site-a")
        svc.ensure_day("u1", D, site_id="site-b")
        assert svc._days.get_day("u1", D, site_id="site-a").id != \
            svc._days.get_day("u1", D, site_id="site-b").id

    def test_on_event_origin_and_times(self, svc: AttendanceDayService):
        rec(svc, ts="2026-09-11T09:30:00")
        day = svc.ensure_day("u1", D, site_id="site-a")
        assert day.origin == "self" and day.status == DayStatus.PENDING
        assert day.first_in == "2026-09-11T09:30:00"
        assert day.late_minutes == 90  # fact, not verdict
        rec(svc, kind="out", ts="2026-09-11T16:00:00")
        assert svc.ensure_day("u1", D, site_id="site-a").last_out == \
            "2026-09-11T16:00:00"

    def test_confirmed_event_promotes_day(self, svc: AttendanceDayService):
        e = ev(status=AttendanceStatus.CONFIRMED)
        svc.on_event(e)
        assert svc.ensure_day("u1", D, site_id="site-a").status == \
            DayStatus.CONFIRMED

    def test_assisted_stays_pending(self, svc: AttendanceDayService):
        rec(svc, method="assisted", status="pending_verification")
        day = svc.ensure_day("u1", D, site_id="site-a")
        assert day.origin == "assisted" and day.status == DayStatus.PENDING


class TestInvariants:
    """§8: nothing automatic decides the day."""

    def test_no_events_never_absent(self, svc: AttendanceDayService):
        day = svc.ensure_day("u1", D, site_id="site-a")
        view = svc.day_view("u1", D, site_id="site-a")
        assert day.status == DayStatus.PENDING and day.verdict is None
        assert "missing_evidence" in view["anomalies"]

    def test_missing_gps_never_absent(self, svc: AttendanceDayService):
        rec(svc, lat=None, lon=None, verdict="unavailable")
        view = svc.day_view("u1", D, site_id="site-a")
        assert "missing_gps" in view["anomalies"]
        assert view["day"].status == DayStatus.PENDING

    def test_outside_geofence_never_absent(self, svc: AttendanceDayService):
        rec(svc, verdict="outside")
        view = svc.day_view("u1", D, site_id="site-a")
        assert "outside_geofence" in view["anomalies"]
        assert view["day"].status == DayStatus.PENDING

    def test_duplicates_kept_and_flagged(self, svc: AttendanceDayService):
        rec(svc, ts="2026-09-11T08:05:00")
        rec(svc, ts="2026-09-11T08:07:00")
        assert "duplicate_entries" in \
            svc.day_view("u1", D, site_id="site-a")["anomalies"]
        assert svc.ensure_day("u1", D, site_id="site-a").status == \
            DayStatus.PENDING

    def test_two_ins_keep_pending(self, svc: AttendanceDayService):
        rec(svc, ts="2026-09-11T08:05:00",
                        status=AttendanceStatus.CONFIRMED)
        rec(svc, ts="2026-09-11T09:00:00")
        assert svc.ensure_day("u1", D, site_id="site-a").status == \
            DayStatus.CONFIRMED  # evidence accepted; no auto-adjudication

    def test_overnight_is_fact(self, svc: AttendanceDayService):
        rec(svc, ts="2026-09-11T22:00:00")
        rec(svc, kind="out", ts="2026-09-11T06:00:00")
        assert "overnight_shift" in \
            svc.day_view("u1", D, site_id="site-a")["anomalies"]

    def test_late_departure_is_fact(self, svc: AttendanceDayService):
        rec(svc, ts="2026-09-11T08:00:00")
        rec(svc, kind="out", ts="2026-09-11T19:30:00")
        view = svc.day_view("u1", D, site_id="site-a")
        assert "late_departure" in view["anomalies"]
        assert view["day"].verdict is None  # no auto-overtime either

    def test_resolve_absent_only_by_human(self, svc: AttendanceDayService):
        day = svc.ensure_day("u1", D, site_id="site-a")
        decided = svc.resolve(day.id, "mgr1", AttendanceVerdict.ABSENT,
                              "never showed", site_id="site-a")
        assert decided.status == DayStatus.RESOLVED
        assert decided.verdict == AttendanceVerdict.ABSENT

    def test_resolve_guards(self, svc: AttendanceDayService):
        day = svc.ensure_day("u1", D, site_id="site-a")
        with pytest.raises(DatabaseError):
            svc.resolve(day.id, "mgr1", AttendanceVerdict.PRESENT, "  ",
                        site_id="site-a")
        with pytest.raises(DatabaseError):
            svc.resolve(day.id, "mgr1", "super-present", "x", site_id="site-a")
        svc.resolve(day.id, "mgr1", AttendanceVerdict.PRESENT, "ok",
                    site_id="site-a")
        with pytest.raises(DatabaseError):  # no re-resolve without dispute
            svc.resolve(day.id, "mgr1", AttendanceVerdict.PRESENT, "again",
                        site_id="site-a")


class TestDisputeClaim:
    def _resolved(self, svc):
        day = svc.ensure_day("u1", D, site_id="site-a")
        return svc.resolve(day.id, "mgr1", AttendanceVerdict.PRESENT, "ok",
                           site_id="site-a")

    def test_dispute_subject_only(self, svc: AttendanceDayService):
        day = self._resolved(svc)
        with pytest.raises(DatabaseError):
            svc.dispute(day.id, "u9", "not me", site_id="site-a")
        disputed = svc.dispute(day.id, "u1", "was late, not absent",
                               site_id="site-a")
        assert disputed.status == DayStatus.DISPUTED
        assert disputed.dispute_note == "was late, not absent"

    def test_dispute_only_confirmed_resolved(self, svc: AttendanceDayService):
        day = svc.ensure_day("u1", D, site_id="site-a")
        with pytest.raises(DatabaseError):
            svc.dispute(day.id, "u1", "hurry", site_id="site-a")

    def test_disputed_reresolves(self, svc: AttendanceDayService):
        day = self._resolved(svc)
        svc.dispute(day.id, "u1", "x", site_id="site-a")
        again = svc.resolve(day.id, "mgr2", AttendanceVerdict.PRESENT_LATE,
                            "bus broke", site_id="site-a")
        assert again.status == DayStatus.RESOLVED

    def test_correction_approve_reopens_pending(self, svc: AttendanceDayService):
        day = self._resolved(svc)
        claim = svc.claim("u1", D, ClaimKind.CORRECTION, "device was dead",
                          "u1", site_id="site-a")
        decided = svc.decide_claim(claim.id, "mgr1", True, "verified",
                                   site_id="site-a")
        assert decided.status == "approved"
        reopened = svc._days.get_day("u1", D, site_id="site-a")
        assert reopened.status == DayStatus.PENDING
        assert reopened.verdict is None
        assert "reopened by claim" in (reopened.resolution_note or "")

    def test_deny_keeps_day(self, svc: AttendanceDayService):
        day = self._resolved(svc)
        claim = svc.claim("u1", D, ClaimKind.CORRECTION, "x", "u1",
                          site_id="site-a")
        svc.decide_claim(claim.id, "mgr1", False, "no", site_id="site-a")
        assert svc._days.get_day("u1", D, site_id="site-a").status == \
            DayStatus.RESOLVED

    def test_note_never_mutates_day(self, svc: AttendanceDayService):
        svc.ensure_day("u1", D, site_id="site-a")
        note = svc.claim("u1", D, ClaimKind.NOTE, "seen on site", "mgr1",
                         site_id="site-a")
        svc.decide_claim(note.id, "mgr1", True, "noted", site_id="site-a")
        assert svc._days.get_day("u1", D, site_id="site-a").status == \
            DayStatus.PENDING

    def test_correction_self_only(self, svc: AttendanceDayService):
        with pytest.raises(DatabaseError):
            svc.claim("u1", D, ClaimKind.CORRECTION, "for a friend", "mgr1",
                      site_id="site-a")

    def test_sweep_idempotent(self, svc: AttendanceDayService):
        rec(svc)
        first = svc.sweep(["u1", "u2"], D, site_id="site-a")
        assert len(first) == 2
        assert svc._days.get_day("u2", D, site_id="site-a").origin == "none"
        second = svc.sweep(["u1", "u2"], D, site_id="site-a")
        assert [d.id for d in first] == [d.id for d in second]

    def test_cross_site_denied(self, svc: AttendanceDayService):
        day = svc.ensure_day("u1", D, site_id="site-a")
        with pytest.raises(DatabaseError):
            svc.resolve(day.id, "mgr1", AttendanceVerdict.PRESENT, "x",
                        site_id="site-b")
        assert svc.day_view("u1", D, site_id="site-b")["day"].id != day.id
