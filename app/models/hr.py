"""HR request models (advance + transport allowance).

Chain: pending -> pm_confirmed -> approved | rejected.
Delegation: PM assigns to HQ HR once (delegated flag blocks re-delegation).
Signatures: every full-role decision appends {role, chat_id, name,
decision, note, at} to the signatures JSON list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class HRRequestType:
    ADVANCE = "advance"
    TRANSPORT = "transport"


class HRRequestStatus:
    PENDING = "pending"
    PM_CONFIRMED = "pm_confirmed"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class HRRequest:
    """A salary-advance or transport-allowance request."""

    requester_chat_id: str
    """Telegram chat ID of the requester."""

    requester_name: str
    """Display name of the requester."""

    request_type: str
    """'advance' or 'transport'."""

    amount: float
    """Requested amount."""

    reason: str
    """Advance reason or trip reason."""

    site_id: Optional[str] = None
    """Tenant site (env SITE_ID when unset)."""

    # Transport-only fields
    trip_date: Optional[str] = None
    """Trip date (YYYY-MM-DD)."""
    report_ref: Optional[str] = None
    """Linked report reference (e.g. report date)."""
    receipt_path: Optional[str] = None
    """Local path of the downloaded receipt photo."""

    # Advance-only fields
    deduction_month: Optional[str] = None
    """YYYY-MM picked by the approver on approval."""

    # Chain state
    status: str = HRRequestStatus.PENDING
    assigned_to: Optional[str] = None
    """Chat ID of the current approver (PM first, then HR)."""
    delegated: bool = False
    """True once a PM delegated to HQ HR (no re-delegation)."""
    note: Optional[str] = None
    """Rejection note or approver comment."""
    signatures: list = field(default_factory=list)
    """Full-role decision signatures (dicts)."""
    pdf_path: Optional[str] = None
    """Approved printable PDF path."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: Optional[str] = None
    id: Optional[int] = None
