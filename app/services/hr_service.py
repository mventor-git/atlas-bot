"""HR chain engine (advance + transport): pure logic, no Telegram I/O.

Chain: pending -> pm_confirmed -> approved | rejected.
PM may delegate once to HQ HR (delegated flag blocks re-delegation).
Every full-role decision appends a digital signature.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from app.database import driver
from app.models.hr import HRRequest, HRRequestStatus, HRRequestType
from app.repositories.hr_repository import HRRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _sign(request: HRRequest, role: str, chat_id: str, name: str,
          decision: str, note: Optional[str] = None) -> None:
    request.signatures = list(request.signatures or []) + [{
        "role": role,
        "chat_id": chat_id,
        "name": name,
        "decision": decision,
        "note": note or "",
        "at": datetime.now().isoformat(),
    }]


class HRService:
    """Approval-chain engine for HR requests."""

    def __init__(self, hr_repo: HRRepository) -> None:
        self._repo = hr_repo

    # --- Request ---

    def request_advance(
        self, chat_id: str, name: str, amount: float, reason: str,
        site_id: str | None = None,
    ) -> HRRequest:
        """File a salary-advance request (amount + reason set by requester)."""
        self._check_amount(amount)
        if not (reason or "").strip():
            raise DatabaseError("Advance reason is required.")
        return self._repo.add(HRRequest(
            requester_chat_id=chat_id,
            requester_name=name,
            request_type=HRRequestType.ADVANCE,
            amount=float(amount),
            reason=reason.strip(),
            site_id=site_id or driver.site_id(),
        ))

    def request_transport(
        self, chat_id: str, name: str, amount: float, reason: str,
        trip_date: str, report_ref: str = "", receipt_path: str = "",
        site_id: str | None = None,
    ) -> HRRequest:
        """File a transport-allowance request."""
        self._check_amount(amount)
        if not (reason or "").strip():
            raise DatabaseError("Trip reason is required.")
        if not (trip_date or "").strip():
            raise DatabaseError("Trip date is required.")
        return self._repo.add(HRRequest(
            requester_chat_id=chat_id,
            requester_name=name,
            request_type=HRRequestType.TRANSPORT,
            amount=float(amount),
            reason=reason.strip(),
            trip_date=trip_date.strip(),
            report_ref=(report_ref or "").strip(),
            receipt_path=(receipt_path or "").strip(),
            site_id=site_id or driver.site_id(),
        ))

    # --- Chain transitions ---

    def confirm_pm(
        self, request_id: int, pm_chat_id: str, pm_name: str,
        site_id: str | None = None,
    ) -> HRRequest:
        """Site PM confirms (gate 1). Moves pending -> pm_confirmed."""
        req = self._get(request_id, site_id)
        if req.status != HRRequestStatus.PENDING:
            raise DatabaseError(
                f"Request {request_id} is {req.status}, PM confirm needs pending.")
        req.status = HRRequestStatus.PM_CONFIRMED
        req.assigned_to = None  # back to the HR queue
        _sign(req, "project_manager", pm_chat_id, pm_name, "confirmed")
        return self._repo.save(req)

    def delegate_to_hr(
        self, request_id: int, pm_chat_id: str, pm_name: str,
        hr_chat_id: str, site_id: str | None = None,
    ) -> HRRequest:
        """PM hands the request to HQ HR (single hop, no re-delegation)."""
        req = self._get(request_id, site_id)
        if req.status not in (HRRequestStatus.PENDING, HRRequestStatus.PM_CONFIRMED):
            raise DatabaseError(
                f"Request {request_id} is {req.status}, cannot delegate.")
        if req.delegated:
            raise DatabaseError(
                f"Request {request_id} was already delegated - HR cannot delegate.")
        req.delegated = True
        req.assigned_to = hr_chat_id
        if req.status == HRRequestStatus.PENDING:
            req.status = HRRequestStatus.PM_CONFIRMED
        _sign(req, "project_manager", pm_chat_id, pm_name,
              "delegated", note=f"to {hr_chat_id}")
        return self._repo.save(req)

    def decide_hr(
        self, request_id: int, hr_chat_id: str, hr_name: str,
        approve: bool, note: str = "",
        deduction_month: str = "", site_id: str | None = None,
    ) -> HRRequest:
        """HQ HR approves or rejects (gate 2, final)."""
        req = self._get(request_id, site_id)
        if req.status != HRRequestStatus.PM_CONFIRMED:
            raise DatabaseError(
                f"Request {request_id} is {req.status}, HR decision needs PM confirmation.")
        if approve:
            if req.request_type == HRRequestType.ADVANCE:
                if not _MONTH_RE.match(deduction_month or ""):
                    raise DatabaseError(
                        "Approving an advance requires deduction_month as YYYY-MM.")
                req.deduction_month = deduction_month
            req.status = HRRequestStatus.APPROVED
            _sign(req, "hr", hr_chat_id, hr_name, "approved", note=note)
        else:
            req.status = HRRequestStatus.REJECTED
            _sign(req, "hr", hr_chat_id, hr_name, "rejected", note=note)
            req.note = note or req.note
        return self._repo.save(req)

    def record_pdf(self, request_id: int, pdf_path: str,
                   site_id: str | None = None) -> HRRequest:
        """Attach the rendered printable PDF to an approved request."""
        req = self._get(request_id, site_id)
        if req.status != HRRequestStatus.APPROVED:
            raise DatabaseError("PDF attaches only to approved requests.")
        req.pdf_path = pdf_path
        return self._repo.save(req)

    # --- Visibility ---

    def visible_to(self, chat_id: str, role: str,
                   site_id: str | None = None) -> list[HRRequest]:
        """Requests visible to a user: own always; full roles see the site queue."""
        site = site_id or driver.site_id()
        own = self._repo.list_for_requester(chat_id, site_id=site)
        if role in ("superadmin", "project_manager", "hr", "admin"):
            queued = self._repo.list_pending(site_id=site)
            seen = {r.id for r in own}
            return own + [r for r in queued if r.id not in seen]
        return own

    def history_for_user(
        self, chat_id: str, window: str = "month",
        site_id: str | None = None, today: str = "",
    ) -> list[HRRequest]:
        """Request history for month/week/3mo/year windows (by created_at date)."""
        from datetime import date as _date

        rows = self._repo.list_for_requester(chat_id, site_id=site_id or driver.site_id())
        ref = today or _date.today().isoformat()
        try:
            ref_d = _date.fromisoformat(ref[:10])
        except ValueError:
            ref_d = _date.today()
        if window == "week":
            cutoff = ref_d.isocalendar()[1]  # ISO week number compare below
            out = [r for r in rows if self._week_of(r.created_at) == cutoff
                   and (r.created_at or "")[:4] == ref_d.isoformat()[:4]]
        elif window == "3mo":
            out = [r for r in rows if (r.created_at or "")[:7] >= _shift_month(ref_d, 2)]
        elif window == "year":
            out = [r for r in rows if (r.created_at or "")[:4] == ref_d.isoformat()[:4]]
        else:  # month
            out = [r for r in rows if (r.created_at or "")[:7] == ref_d.isoformat()[:7]]
        return out

    # --- Internals ---

    def _get(self, request_id: int, site_id: str | None) -> HRRequest:
        req = self._repo.get_by_id(request_id, site_id=site_id or driver.site_id())
        if req is None:
            raise DatabaseError(f"HR request {request_id} not found in this site.")
        return req

    @staticmethod
    def _check_amount(amount: float) -> None:
        try:
            value = float(amount)
        except (TypeError, ValueError):
            raise DatabaseError("Amount must be a number.")
        if value <= 0:
            raise DatabaseError("Amount must be greater than zero.")

    @staticmethod
    def _week_of(created_at: str) -> int:
        from datetime import date as _date

        try:
            return _date.fromisoformat((created_at or "")[:10]).isocalendar()[1]
        except ValueError:
            return -1


def _shift_month(ref, months_back: int) -> str:
    """YYYY-MM string N months back from a date."""
    year, month = ref.year, ref.month - months_back
    while month < 1:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"
