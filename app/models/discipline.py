"""Discipline domain models (ticket-017, 014c).

Top-down: HQ files against an employee. The filer never decides
(separation of duties); the subject may appeal a decision.
Severity/action taxonomy is free-text until the owner defines it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class DisciplineStatus:
    FILED = "filed"
    UNDER_REVIEW = "under_review"
    DECIDED = "decided"
    APPEALED = "appealed"


@dataclass
class DisciplineCase:
    """One disciplinary case against an employee."""

    subject_chat_id: str
    """Employee the case is filed against (appeals as subject)."""

    filed_by: str
    """HQ filer (can review, never decide own filing)."""

    summary: str

    site_id: Optional[str] = None
    status: str = DisciplineStatus.FILED
    reviewed_by: Optional[str] = None
    decided_by: Optional[str] = None
    decision: Optional[str] = None
    """Decided outcome, free-text until owner taxonomy lands."""
    decision_note: Optional[str] = None
    appeal_note: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    decided_at: Optional[str] = None
    id: Optional[int] = None
