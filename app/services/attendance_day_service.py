"""Attendance day service (026): aggregates above the 013 event layer.

Hard invariants (§8), enforced here and tested:
- Missing GPS / outside geofence / no network / no events are NEVER
  transcribed into an absence; they stay origin/anomaly metadata.
- 'absent' exists ONLY as a human verdict via resolve() — no event,
  cutoff, timer, or check-out-after-hours path writes it.
- Late arrival, overnight shifts, and late departures are computed FACTS
  in day_view, never punishments and never auto-overtime.
- Presence after checkout never approves overtime (payable overtime lives
  in the HR overtime-request chain, 011).
- Corrections affect a resolved day only back to pending (re-resolution
  required); manager notes never mutate the day.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from app.database import driver
from app.models.attendance import (
    AttendanceClaim,
    AttendanceDay,
    AttendanceEvent,
    AttendanceStatus,
    AttendanceVerdict,
    ClaimKind,
    ClaimStatus,
    DayStatus,
)
from app.repositories.attendance_day_repository import AttendanceDayRepository
from app.repositories.attendance_repository import AttendanceRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

RESOLVABLE = (DayStatus.PENDING, DayStatus.CONFIRMED, DayStatus.DISPUTED)
VERDICTS = (
    AttendanceVerdict.PRESENT, AttendanceVerdict.PRESENT_LATE,
    AttendanceVerdict.ABSENT, AttendanceVerdict.EXCUSED,
    AttendanceVerdict.ON_MISSION, AttendanceVerdict.AUTHORIZED_OUTSIDE,
)


class AttendanceDayService:
    """Day aggregates, anomalies, claims, disputes, human resolution."""

    def __init__(self, day_repo: AttendanceDayRepository,
                 event_repo: AttendanceRepository,
                 expected_start: time = time(8, 0),
                 cutoff: time = time(17, 0)) -> None:
        self._days = day_repo
        self._events = event_repo
        self._expected_start = expected_start
        self._cutoff = cutoff

    # --- ensures ---

    def ensure_day(self, chat_id: str, day_date: str,
                   site_id: str | None = None) -> AttendanceDay:
        """Fetch the day row, creating it pending when absent (idempotent)."""
        day = self._days.get_day(chat_id, day_date, site_id=site_id)
        if day is not None:
            return day
        return self._days.add_day(AttendanceDay(
            chat_id=chat_id, day_date=day_date,
            site_id=site_id or driver.site_id()))

    def sweep(self, chats: list[str], day_date: str,
              site_id: str | None = None) -> list[AttendanceDay]:
        """Ensure pending days for roster chats with none recorded.

        Idempotent: call whenever the queue is viewed; scheduled nightly
        runs come with the persistent notification engine (Phase 6).
        """
        return [self.ensure_day(c, day_date, site_id=site_id) for c in chats]

    def on_event(self, event: AttendanceEvent) -> AttendanceDay:
        """Fold one 013 event into its day (origin + times + promotion)."""
        day = self.ensure_day(event.chat_id, event.event_date,
                              site_id=event.site_id)
        if event.method == "assisted":
            day.origin = "assisted"
        elif day.origin == "none":
            day.origin = "self"
        ts = event.created_at or ""
        if event.check_type == "in":
            if not day.first_in or ts < day.first_in:
                day.first_in = ts
        else:
            if not day.last_out or ts > day.last_out:
                day.last_out = ts
        day.late_minutes = self._late_minutes(day.first_in)
        if day.status == DayStatus.PENDING and \
                event.status in (AttendanceStatus.CONFIRMED,
                                 AttendanceStatus.RESOLVED):
            day.status = DayStatus.CONFIRMED
        return self._days.update_day(day)

    # --- human resolution ---

    def resolve(self, day_id: int, actor: str, verdict: str, note: str,
                site_id: str | None = None) -> AttendanceDay:
        """Human verdict. Handlers gate manage_attendance@site; this service
        additionally requires explicit state edges and a note."""
        day = self._get(day_id, site_id)
        if day.status not in RESOLVABLE:
            raise DatabaseError(
                f"Day {day_id} is {day.status}; resolution needs "
                "pending/confirmed/disputed (dispute first to reopen).")
        if verdict not in VERDICTS:
            raise DatabaseError(f"Unknown verdict: {verdict}")
        if not (note or "").strip():
            raise DatabaseError("Resolutions require a note.")
        day.status = DayStatus.RESOLVED
        day.verdict = verdict
        day.resolution_note = note.strip()
        day.resolved_by = actor
        return self._days.update_day(day)

    def dispute(self, day_id: int, actor: str, note: str,
                site_id: str | None = None) -> AttendanceDay:
        """Subject reopens a confirmed/resolved day for re-resolution."""
        day = self._get(day_id, site_id)
        if day.status not in (DayStatus.CONFIRMED, DayStatus.RESOLVED):
            raise DatabaseError(
                f"Day {day_id} is {day.status}; only confirmed/resolved "
                "days can be disputed.")
        if str(day.chat_id) != str(actor):
            raise DatabaseError("Only the employee may dispute their day.")
        if not (note or "").strip():
            raise DatabaseError("Disputes require a note.")
        day.status = DayStatus.DISPUTED
        day.dispute_note = note.strip()
        return self._days.update_day(day)

    # --- claims ---

    def claim(self, chat_id: str, day_date: str, kind: str, text: str,
              raised_by: str, site_id: str | None = None) -> AttendanceClaim:
        if kind not in (ClaimKind.CORRECTION, ClaimKind.NOTE):
            raise DatabaseError(f"Unknown claim kind: {kind}")
        if not (text or "").strip():
            raise DatabaseError("Claims require text.")
        if kind == ClaimKind.CORRECTION and str(raised_by) != str(chat_id):
            raise DatabaseError("Correction claims are self-only.")
        self.ensure_day(chat_id, day_date, site_id=site_id)
        return self._days.add_claim(AttendanceClaim(
            chat_id=chat_id, day_date=day_date, kind=kind,
            text=text.strip(), raised_by=raised_by,
            site_id=site_id or driver.site_id()))

    def decide_claim(self, claim_id: int, actor: str, approve: bool, note: str,
                     site_id: str | None = None) -> AttendanceClaim:
        claim = self._claim(claim_id, site_id)
        if claim.status != ClaimStatus.OPEN:
            raise DatabaseError(f"Claim {claim_id} is {claim.status} already.")
        if not (note or "").strip():
            raise DatabaseError("Claim decisions require a note.")
        day = self._days.get_day(claim.chat_id, claim.day_date,
                                 site_id=claim.site_id)
        claim.status = ClaimStatus.APPROVED if approve else ClaimStatus.DENIED
        claim.decided_by = actor
        claim.decision_note = note.strip()
        self._days.update_claim(claim)
        if approve and claim.kind == ClaimKind.CORRECTION and day is not None \
                and day.status in (DayStatus.CONFIRMED, DayStatus.RESOLVED):
            # Approval never adjudicates: it reopens for re-resolution.
            old = day.resolution_note or ""
            day.status = DayStatus.PENDING
            day.verdict = None
            day.resolved_by = None
            day.resolution_note = f"[reopened by claim #{claim.id}] {old}" \
                .strip() or None
            self._days.update_day(day)
        return claim

    # --- read API (handlers, `/myday`, queues, Phase 5) ---

    def get_day(self, day_id: int,
                site_id: str | None = None) -> Optional[AttendanceDay]:
        return self._days.get_day_by_id(
            day_id, site_id=site_id or driver.site_id())

    def open_claims(self, site_id: str | None = None) -> list:
        return self._days.open_claims(site_id=site_id)

    def day_view(self, chat_id: str, day_date: str,
                 site_id: str | None = None) -> dict:
        """Day + events + derived anomalies + claims (read-only)."""
        day = self.ensure_day(chat_id, day_date, site_id=site_id)
        events = self._events.for_user_day(chat_id, day_date,
                                           site_id=day.site_id)
        claims = self._days.claims_for_day(chat_id, day_date,
                                           site_id=day.site_id)
        return {
            "day": day,
            "events": events,
            "anomalies": self._anomalies(day, events),
            "claims": claims,
        }

    def queue(self, day_date: str,
              site_id: str | None = None) -> list[AttendanceDay]:
        """Days needing human action: pending or disputed only."""
        rows = self._days.days_for_site_day(day_date, site_id=site_id)
        return [d for d in rows
                if d.status in (DayStatus.PENDING, DayStatus.DISPUTED)]

    # --- derived facts ---

    def _anomalies(self, day: AttendanceDay,
                   events: list[AttendanceEvent]) -> list[str]:
        out: list[str] = []
        if not events:
            out.append("missing_evidence")
            return out
        ins = [e for e in events if e.check_type == "in"]
        outs = [e for e in events if e.check_type == "out"]
        if any((e.latitude is None or e.longitude is None) for e in events):
            out.append("missing_gps")
        if any(e.location_verdict == "outside" for e in events):
            out.append("outside_geofence")
        if any(e.location_verdict == "anomaly" for e in events):
            out.append("fix_anomaly")
        if any(e.status == "exception" for e in events):
            out.append("device_exception")
        if len(ins) > 1:
            out.append("duplicate_entries")
        if day.first_in and day.last_out and day.last_out <= day.first_in:
            out.append("overnight_shift")
        for e in outs:
            if (e.created_at or "")[:10] == day.day_date and \
                    self._clock(e.created_at) >= self._clock_of(self._cutoff):
                out.append("late_departure")
                break
        return out

    def _late_minutes(self, first_in: str | None) -> int | None:
        if not first_in:
            return None
        delta = self._clock(first_in) - self._clock_of(self._expected_start)
        return max(0, delta)

    @staticmethod
    def _clock(ts: str | None) -> int:
        """HH:MM of an ISO timestamp, as minutes (never a verdict)."""
        try:
            hh, mm = (ts or "")[11:16].split(":")
            return int(hh) * 60 + int(mm)
        except (ValueError, AttributeError):
            return 0

    @staticmethod
    def _clock_of(t: time) -> int:
        return t.hour * 60 + t.minute

    # --- internals ---

    def _get(self, day_id: int, site_id: str | None) -> AttendanceDay:
        day = self._days.get_day_by_id(day_id,
                                       site_id=site_id or driver.site_id())
        if day is None:
            raise DatabaseError(f"Attendance day {day_id} not found in this site.")
        return day

    def _claim(self, claim_id: int, site_id: str | None) -> AttendanceClaim:
        claim = self._days.get_claim(claim_id,
                                     site_id=site_id or driver.site_id())
        if claim is None:
            raise DatabaseError(f"Claim {claim_id} not found in this site.")
        return claim
