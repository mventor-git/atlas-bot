"""Case domain models (ticket-014, P6a).

Employee-filed cases (grievance/complaint/suggestion/resignation) share one
chain: filed -> under_review -> resolved, with filer appeal reopening to
under_review. Discipline is top-down and lives elsewhere (014c).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class CaseType:
    GRIEVANCE = "grievance"
    COMPLAINT = "complaint"
    SUGGESTION = "suggestion"
    RESIGNATION = "resignation"


KNOWN_TYPES = frozenset({
    CaseType.GRIEVANCE, CaseType.COMPLAINT,
    CaseType.SUGGESTION, CaseType.RESIGNATION,
})

# case type -> SELF submit capability (handlers gate on these; service is auth-free).
SUBMIT_CAPABILITY = {
    CaseType.GRIEVANCE: "submit_grievance",
    CaseType.COMPLAINT: "submit_complaint",
    CaseType.SUGGESTION: "submit_suggestion",
    CaseType.RESIGNATION: "submit_resignation",
}


class CaseStatus:
    FILED = "filed"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    APPEALED = "appealed"


@dataclass
class Case:
    """One filed case with its review trail."""

    reporter_chat_id: str
    case_type: str
    summary: str

    site_id: Optional[str] = None
    status: str = CaseStatus.FILED
    reviewed_by: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_note: Optional[str] = None
    appeal_note: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: Optional[str] = None
    id: Optional[int] = None
