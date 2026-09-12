"""Case service (014): file -> review -> resolve -> appeal.

Rules enforced here:
- Unknown types and blank summaries are rejected at filing.
- Nothing resolves without triage (filed must pass through under_review).
- Resolutions require a note (accountability, never silent).
- Only the original filer may appeal, and only from resolved.
- Reads/writes are site-scoped; cross-site access raises.
"""

from __future__ import annotations

from typing import Optional

from app.database import driver
from app.models.case import KNOWN_TYPES, Case, CaseStatus
from app.repositories.case_repository import CaseRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CaseService:
    """Employee-filed case workflow (handlers gate capabilities)."""

    def __init__(self, case_repo: CaseRepository) -> None:
        self._repo = case_repo

    # --- Filing ---

    def file(self, reporter_chat_id: str, case_type: str, summary: str,
             site_id: str | None = None) -> Case:
        """File a new case (starts at filed, untriaged)."""
        if case_type not in KNOWN_TYPES:
            raise DatabaseError(f"Unknown case type: {case_type}")
        if not (summary or "").strip():
            raise DatabaseError("Cases require a summary.")
        return self._repo.add(Case(
            reporter_chat_id=reporter_chat_id, case_type=case_type,
            summary=summary.strip(), status=CaseStatus.FILED,
            site_id=site_id or driver.site_id(),
        ))

    # --- Review workflow ---

    def review(self, case_id: int, reviewer_chat_id: str,
               site_id: str | None = None) -> Case:
        """Take a filed/appealed case into review."""
        case = self._get(case_id, site_id)
        if case.status not in (CaseStatus.FILED, CaseStatus.APPEALED):
            raise DatabaseError(
                f"Case {case_id} is {case.status}, cannot take into review.")
        case.status = CaseStatus.UNDER_REVIEW
        case.reviewed_by = reviewer_chat_id
        return self._repo.update(case)

    def resolve(self, case_id: int, resolver_chat_id: str, note: str,
                site_id: str | None = None) -> Case:
        """Resolve a reviewed case with a mandatory resolution note."""
        case = self._get(case_id, site_id)
        if case.status not in (CaseStatus.UNDER_REVIEW, CaseStatus.APPEALED):
            raise DatabaseError(
                f"Case {case_id} is {case.status}, resolve needs "
                "under_review/appealed.")
        if not (note or "").strip():
            raise DatabaseError("Resolutions require a note.")
        case.status = CaseStatus.RESOLVED
        case.resolved_by = resolver_chat_id
        case.resolution_note = note.strip()
        return self._repo.update(case)

    def appeal(self, case_id: int, filer_chat_id: str, note: str,
               site_id: str | None = None) -> Case:
        """Filer reopens a resolved case for re-review."""
        case = self._get(case_id, site_id)
        if case.status != CaseStatus.RESOLVED:
            raise DatabaseError(
                f"Case {case_id} is {case.status}, only resolved cases appeal.")
        if str(case.reporter_chat_id) != str(filer_chat_id):
            raise DatabaseError("Only the filer may appeal this case.")
        if not (note or "").strip():
            raise DatabaseError("Appeals require a note.")
        case.status = CaseStatus.APPEALED
        case.appeal_note = note.strip()
        return self._repo.update(case)

    # --- Queries (public for handlers; Phase 1 decoupling) ---

    def get(self, case_id: int, site_id: str | None = None) -> Optional[Case]:
        return self._repo.get_by_id(case_id, site_id=site_id)

    def open_cases(self, site_id: str | None = None) -> list:
        return self._repo.open_cases(site_id=site_id)

    def list_mine(self, chat_id: str,
                  site_id: str | None = None) -> list:
        return self._repo.for_reporter(chat_id, site_id=site_id)

    # --- Internals ---

    def _get(self, case_id: int, site_id: str | None) -> Case:
        case = self._repo.get_by_id(case_id, site_id=site_id or driver.site_id())
        if case is None:
            raise DatabaseError(f"Case {case_id} not found in this site.")
        return case
