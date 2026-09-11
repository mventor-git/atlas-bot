"""Attendance service (013): evidence intake, confirmation, resolution.

Rules enforced here:
- Missing GPS never yields absence (UNAVAILABLE evidence, needs confirmation).
- Presence after checkout / outside geofence never auto-approves anything.
- Assisted check-ins are explicit proxy records, confirmed separately.
- Lateness is a computed fact (minutes), never a disciplinary verdict.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from app.database import driver
from app.models.attendance import (
    AttendanceEvent,
    AttendanceStatus,
    AttendanceVerdict,
    LocationVerdict,
)
from app.repositories.attendance_repository import AttendanceRepository
from app.services.geofence import evaluate as evaluate_fix
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AttendanceService:
    """Check-in/out intake, confirmation workflow, and resolution."""

    def __init__(self, attendance_repo: AttendanceRepository,
                 expected_start: time = time(8, 0)) -> None:
        self._repo = attendance_repo
        self._expected_start = expected_start

    @staticmethod
    def _verdict(latitude, longitude, accuracy_m, fence):
        """None when no fence is configured (unchecked); else evaluated."""
        if fence is None and latitude is not None:
            return None
        return evaluate_fix(latitude, longitude, accuracy_m, fence)

    # --- Intake ---

    def check_in(
        self, chat_id: str, event_date: str,
        latitude: float | None = None, longitude: float | None = None,
        accuracy_m: float | None = None, fence: dict | None = None,
        site_id: str | None = None,
    ) -> AttendanceEvent:
        """Record a check-in attempt with location evidence."""
        verdict = self._verdict(latitude, longitude, accuracy_m, fence)
        return self._repo.add(AttendanceEvent(
            chat_id=chat_id, event_date=event_date, check_type="in",
            method="self", latitude=latitude, longitude=longitude,
            accuracy_m=accuracy_m, location_verdict=verdict,
            status=AttendanceStatus.SUBMITTED,
            site_id=site_id or driver.site_id(),
        ))

    def check_out(self, chat_id: str, event_date: str,
                  latitude: float | None = None,
                  longitude: float | None = None,
                  accuracy_m: float | None = None,
                  fence: dict | None = None,
                  site_id: str | None = None) -> AttendanceEvent:
        """Record a check-out attempt (presence after checkout is NOT overtime)."""
        verdict = self._verdict(latitude, longitude, accuracy_m, fence)
        return self._repo.add(AttendanceEvent(
            chat_id=chat_id, event_date=event_date, check_type="out",
            method="self", latitude=latitude, longitude=longitude,
            accuracy_m=accuracy_m, location_verdict=verdict,
            status=AttendanceStatus.SUBMITTED,
            site_id=site_id or driver.site_id(),
        ))

    def assisted_check_in(
        self, target_chat_id: str, initiated_by: str, event_date: str,
        reason: str, latitude: float | None = None,
        longitude: float | None = None, accuracy_m: float | None = None,
        fence: dict | None = None, site_id: str | None = None,
    ) -> AttendanceEvent:
        """Explicit proxy check-in. Never silent, never auto-final."""
        if not (reason or "").strip():
            raise DatabaseError("Assisted check-in requires a reason.")
        verdict = self._verdict(latitude, longitude, accuracy_m, fence)
        return self._repo.add(AttendanceEvent(
            chat_id=target_chat_id, event_date=event_date, check_type="in",
            method="assisted", latitude=latitude, longitude=longitude,
            accuracy_m=accuracy_m, location_verdict=verdict,
            assisted_target_chat_id=target_chat_id,
            assisted_reason=reason.strip(), initiated_by=initiated_by,
            status=AttendanceStatus.PENDING_VERIFICATION,
            site_id=site_id or driver.site_id(),
        ))

    # --- Confirmation workflow ---

    def confirm(self, event_id: int, confirmer_chat_id: str,
                site_id: str | None = None) -> AttendanceEvent:
        """Confirmer approves an attempt (or assisted record)."""
        event = self._get(event_id, site_id)
        if event.status not in (AttendanceStatus.SUBMITTED,
                                AttendanceStatus.PENDING_VERIFICATION,
                                AttendanceStatus.DISPUTED):
            raise DatabaseError(f"Event {event_id} is {event.status}, cannot confirm.")
        event.status = AttendanceStatus.CONFIRMED
        event.confirmed_by = confirmer_chat_id
        return self._repo.update(event)

    def dispute(self, event_id: int, confirmer_chat_id: str, note: str,
                site_id: str | None = None) -> AttendanceEvent:
        """Confirmer disputes an attempt with a reason."""
        event = self._get(event_id, site_id)
        if not (note or "").strip():
            raise DatabaseError("Disputes require a note.")
        event.status = AttendanceStatus.DISPUTED
        event.confirmed_by = confirmer_chat_id
        event.note = note.strip()
        return self._repo.update(event)

    def mark_exception(self, event_id: int, confirmer_chat_id: str, note: str,
                       site_id: str | None = None) -> AttendanceEvent:
        """Record an exception (device/network/anomaly) with cause."""
        event = self._get(event_id, site_id)
        if not (note or "").strip():
            raise DatabaseError("Exceptions require a cause note.")
        event.status = AttendanceStatus.EXCEPTION
        event.confirmed_by = confirmer_chat_id
        event.note = note.strip()
        return self._repo.update(event)

    def resolve(self, event_id: int, verdict: str,
                resolver_chat_id: str, site_id: str | None = None) -> AttendanceEvent:
        """Resolve to a final verdict (present/late/absent/excused/...)."""
        valid = (
            AttendanceVerdict.PRESENT, AttendanceVerdict.PRESENT_LATE,
            AttendanceVerdict.ABSENT, AttendanceVerdict.EXCUSED,
            AttendanceVerdict.ON_MISSION, AttendanceVerdict.AUTHORIZED_OUTSIDE,
        )
        if verdict not in valid:
            raise DatabaseError(f"Unknown verdict: {verdict}")
        event = self._get(event_id, site_id)
        if event.status not in (AttendanceStatus.CONFIRMED,
                                AttendanceStatus.EXCEPTION,
                                AttendanceStatus.DISPUTED):
            raise DatabaseError(
                f"Event {event_id} is {event.status}, resolve needs "
                "confirmed/exception/disputed.")
        event.status = AttendanceStatus.RESOLVED
        event.verdict = verdict
        event.confirmed_by = resolver_chat_id
        return self._repo.update(event)

    # --- Derived facts ---

    def late_minutes(self, check_time: time) -> int:
        """Minutes past expected start (0 when on time). Pure fact."""
        delta = (check_time.hour * 60 + check_time.minute) - (
            self._expected_start.hour * 60 + self._expected_start.minute)
        return max(0, delta)

    def record_lateness(self, event_id: int, check_time: time,
                        site_id: str | None = None) -> AttendanceEvent:
        """Attach computed lateness to a confirmed event (fact, not punishment)."""
        event = self._get(event_id, site_id)
        event.late_minutes = self.late_minutes(check_time)
        return self._repo.update(event)

    # --- Internals ---

    def _get(self, event_id: int, site_id: str | None) -> AttendanceEvent:
        event = self._repo.get_by_id(event_id, site_id=site_id or driver.site_id())
        if event is None:
            raise DatabaseError(f"Attendance event {event_id} not found in this site.")
        return event
