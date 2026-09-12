"""Notification outbox domain (ticket-031, Stage 4).

Durable rows replace the in-memory _sent_today set. A notification is
identified for deduplication by a deterministic key:
    site | date | recipient | type | object | level
No random IDs anywhere on the dedup path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class NotificationStatus:
    PENDING = "pending"
    SENDING = "sending"   # claimed by a dispatcher; lease, not a lock server
    SENT = "sent"
    FAILED = "failed"     # terminal: permanent error or retries exhausted


@dataclass
class Notification:
    """One durable notification (outbox row)."""

    dedup_key: str
    recipient: str
    site_id: str
    ntype: str
    """Named type from the catalog (report_missing, ...)."""

    reference: str = ""
    """Domain object the notice is about (report date, case#day, ...)."""
    text: str = ""
    priority: int = 0
    status: str = NotificationStatus.PENDING
    attempts: int = 0
    max_attempts: int = 4
    next_retry_at: Optional[str] = None
    """ISO ts when a failed row becomes due again (None = due now)."""
    claimed_at: Optional[str] = None
    """Lease timestamp of the sending claim (stuck leases expire)."""
    last_error: Optional[str] = None
    """Error category only (never message contents)."""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    sent_at: Optional[str] = None
    acknowledged_at: Optional[str] = None
    id: Optional[int] = None
