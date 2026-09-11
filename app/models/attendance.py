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
