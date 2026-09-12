"""Attendance domain models (ticket-013, P2).

Evidence-first: an attempt carries location evidence; resolution is a
separate explicit step. Presence is never a bare boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class AttendanceStatus:
    SUBMITTED = "submitted"
    PENDING_VERIFICATION = "pending_verification"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    EXCEPTION = "exception"
    RESOLVED = "resolved"


class AttendanceVerdict:
    PRESENT = "present"
    PRESENT_LATE = "present_late"
    ABSENT = "absent"
    EXCUSED = "excused"
    ON_MISSION = "on_mission"
    AUTHORIZED_OUTSIDE = "authorized_outside"


class LocationVerdict:
    INSIDE = "inside"
    OUTSIDE = "outside"
    ANOMALY = "anomaly"
    UNAVAILABLE = "unavailable"


@dataclass
class AttendanceEvent:
    """One attendance attempt with evidence and resolution trail."""

    chat_id: str
    """Telegram chat ID of the employee (or initiator for assisted)."""

    site_id: Optional[str] = None
    event_date: str = ""
    """YYYY-MM-DD."""
    check_type: str = "in"
    """'in' or 'out'."""
    method: str = "self"
    """'self' or 'assisted' - assisted is explicit, never silent."""

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_m: Optional[float] = None
    location_verdict: Optional[str] = None
    """inside/outside/anomaly/unavailable, or None when no fence configured."""

    assisted_target_chat_id: Optional[str] = None
    assisted_reason: Optional[str] = None
    initiated_by: Optional[str] = None

    status: str = AttendanceStatus.SUBMITTED
    verdict: Optional[str] = None
    confirmed_by: Optional[str] = None
    late_minutes: Optional[int] = None
    note: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: Optional[str] = None
    id: Optional[int] = None


class DayStatus:
    """Day-level workflow (026). 'absent' is a VERDICT, never a state."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    RESOLVED = "resolved"


class ClaimKind:
    CORRECTION = "correction"
    NOTE = "note"


class ClaimStatus:
    OPEN = "open"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass
class AttendanceDay:
    """The aggregate truth container for one employee-day (026).

    Events remain the evidence; this row only tracks workflow + the final
    human verdict. Nothing here may be auto-filled from GPS absence.
    """

    chat_id: str
    day_date: str
    """YYYY-MM-DD."""

    site_id: Optional[str] = None
    status: str = DayStatus.PENDING
    origin: str = "none"
    """self | assisted | none - provenance label, never a verdict."""
    first_in: Optional[str] = None
    last_out: Optional[str] = None
    late_minutes: Optional[int] = None
    verdict: Optional[str] = None
    resolution_note: Optional[str] = None
    resolved_by: Optional[str] = None
    dispute_note: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class AttendanceClaim:
    """Correction claim or manager note on one employee-day (026)."""

    chat_id: str
    day_date: str
    kind: str
    """correction (affects the day on approval) | note (observations only)."""
    text: str
    raised_by: str
    """Employee chat_id (correction) or manager chat_id (note)."""

    site_id: Optional[str] = None
    status: str = ClaimStatus.OPEN
    decided_by: Optional[str] = None
    decision_note: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    decided_at: Optional[str] = None
    id: Optional[int] = None
