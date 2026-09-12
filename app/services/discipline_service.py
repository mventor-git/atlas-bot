"""Discipline service (017): file -> review -> decide -> appeal.

Rules enforced here:
- HQ files against an employee, never against themselves.
- Nothing is decided without review (filed must pass through under_review).
- Separation of duties: the filer never decides their own filing.
- Decisions require a note (accountability, never silent).
- Only the subject may appeal, and only from decided.
- Reads/writes are site-scoped; cross-site access raises.
"""

from __future__ import annotations

from typing import Optional

from app.database import driver
from app.models.discipline import DisciplineCase, DisciplineStatus
from app.repositories.discipline_repository import DisciplineRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DisciplineService:
    """HQ-initiated disciplinary workflow (handlers gate capabilities)."""

    def __init__(self, discipline_repo: DisciplineRepository) -> None:
        self._repo = discipline_repo

    # --- Filing ---

    def file(self, subject_chat_id: str, filed_by: str, summary: str,
             site_id: str | None = None) -> DisciplineCase:
        """File a case against an employee (starts at filed, untriaged)."""
        if str(subject_chat_id) == str(filed_by):
            raise DatabaseError("Cannot file a case against yourself.")
        if not (summary or "").strip():
            raise DatabaseError("Cases require a summary.")
        return self._repo.add(DisciplineCase(
            subject_chat_id=subject_chat_id, filed_by=filed_by,
            summary=summary.strip(), status=DisciplineStatus.FILED,
            site_id=site_id or driver.site_id(),
        ))

    # --- Review workflow ---

    def review(self, case_id: int, reviewer_chat_id: str,
               site_id: str | None = None) -> DisciplineCase:
        """Take a filed/appealed case into review."""
        case = self._get(case_id, site_id)
        if case.status not in (DisciplineStatus.FILED, DisciplineStatus.APPEALED):
            raise DatabaseError(
                f"Case {case_id} is {case.status}, cannot take into review.")
        case.status = DisciplineStatus.UNDER_REVIEW
        case.reviewed_by = reviewer_chat_id
        return self._repo.update(case)

    def decide(self, case_id: int, decider_chat_id: str, decision: str,
               note: str, site_id: str | None = None) -> DisciplineCase:
        """Decide a reviewed case. Filer never decides (separation of duties)."""
        case = self._get(case_id, site_id)
        if case.status not in (DisciplineStatus.UNDER_REVIEW,
                               DisciplineStatus.APPEALED):
            raise DatabaseError(
                f"Case {case_id} is {case.status}, decide needs "
                "under_review/appealed.")
        if str(decider_chat_id) == str(case.filed_by):
            raise DatabaseError("Filer cannot decide their own filing.")
        if not (decision or "").strip():
            raise DatabaseError("Decisions require an outcome.")
        if not (note or "").strip():
            raise DatabaseError("Decisions require a note.")
        case.status = DisciplineStatus.DECIDED
        case.decided_by = decider_chat_id
        case.decision = decision.strip()
        case.decision_note = note.strip()
        return self._repo.update(case)

    def appeal(self, case_id: int, subject_chat_id: str, note: str,
               site_id: str | None = None) -> DisciplineCase:
        """Subject reopens a decided case for re-review."""
        case = self._get(case_id, site_id)
        if case.status != DisciplineStatus.DECIDED:
            raise DatabaseError(
                f"Case {case_id} is {case.status}, only decided cases appeal.")
        if str(case.subject_chat_id) != str(subject_chat_id):
            raise DatabaseError("Only the subject may appeal this case.")
        if not (note or "").strip():
            raise DatabaseError("Appeals require a note.")
        case.status = DisciplineStatus.APPEALED
        case.appeal_note = note.strip()
        return self._repo.update(case)

    # --- Queries (public for handlers; Phase 1 decoupling) ---

    def get(self, case_id: int, site_id: str | None = None):
        return self._repo.get_by_id(case_id, site_id=site_id)

    def open_cases(self, site_id: str | None = None) -> list:
        return self._repo.open_cases(site_id=site_id)

    def list_against(self, chat_id: str,
                     site_id: str | None = None) -> list:
        return self._repo.for_subject(chat_id, site_id=site_id)

    # --- Internals ---

    def _get(self, case_id: int, site_id: str | None) -> DisciplineCase:
        case = self._repo.get_by_id(case_id, site_id=site_id or driver.site_id())
        if case is None:
            raise DatabaseError(f"Case {case_id} not found in this site.")
        return case
