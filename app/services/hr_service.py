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
from app.models.hr import (
    DeductionEvent,
    HRRequest,
    HRRequestStatus,
    HRRequestType,
    PayoutEvent,
)
from app.repositories.hr_repository import HRRepository
from app.repositories.money_repository import MoneyRepository
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

    def __init__(self, hr_repo: HRRepository,
                 money_repo: MoneyRepository | None = None) -> None:
        self._repo = hr_repo
        self._money = money_repo

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

    def request_leave(
        self, chat_id: str, name: str, reason: str,
        start_date: str, end_date: str, site_id: str | None = None,
    ) -> HRRequest:
        """File a leave request (date range + reason, no money leg)."""
        start, end = self._check_range(start_date, end_date)
        if not (reason or "").strip():
            raise DatabaseError("Leave reason is required.")
        return self._repo.add(HRRequest(
            requester_chat_id=chat_id,
            requester_name=name,
            request_type=HRRequestType.LEAVE,
            amount=0,
            reason=reason.strip(),
            start_date=start,
            end_date=end,
            site_id=site_id or driver.site_id(),
        ))

    def request_mission(
        self, chat_id: str, name: str, reason: str,
        start_date: str, end_date: str = "", site_id: str | None = None,
    ) -> HRRequest:
        """File a mission request (purpose + dates; payroll effect flagged later)."""
        end = end_date.strip() or start_date.strip()
        start, end = self._check_range(start_date, end)
        if not (reason or "").strip():
            raise DatabaseError("Mission purpose is required.")
        return self._repo.add(HRRequest(
            requester_chat_id=chat_id,
            requester_name=name,
            request_type=HRRequestType.MISSION,
            amount=0,
            reason=reason.strip(),
            start_date=start,
            end_date=end,
            site_id=site_id or driver.site_id(),
        ))

    def request_overtime(
        self, chat_id: str, name: str, date_str: str, hours: float,
        reason: str = "", site_id: str | None = None,
    ) -> HRRequest:
        """File an overtime request (date + hours; presence alone never approves)."""
        try:
            value = float(hours)
        except (TypeError, ValueError):
            raise DatabaseError("Overtime hours must be a number.")
        if value <= 0 or value > 24:
            raise DatabaseError("Overtime hours must be within (0, 24].")
        day = self._check_date(date_str, "Overtime date")
        return self._repo.add(HRRequest(
            requester_chat_id=chat_id,
            requester_name=name,
            request_type=HRRequestType.OVERTIME,
            amount=0,
            reason=(reason or "").strip() or "overtime",
            start_date=day,
            end_date=day,
            hours=value,
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

    # --- Money events (009 ledger) ---

    def _money_or_raise(self) -> MoneyRepository:
        if self._money is None:
            raise DatabaseError("Money ledger not wired for this service.")
        return self._money

    def record_payout(
        self, request_id: int, amount: float, payout_date: str,
        confirmed_by: str, reference: str = "", note: str = "",
        site_id: str | None = None,
    ) -> PayoutEvent:
        """Confirm a payout event (approved requests only)."""
        req = self._get(request_id, site_id)
        if req.status != HRRequestStatus.APPROVED:
            raise DatabaseError("Payouts record only on approved requests.")
        if req.request_type not in (HRRequestType.ADVANCE, HRRequestType.TRANSPORT):
            raise DatabaseError("Only advance/transport requests carry payouts.")
        self._check_amount(amount)
        return self._money_or_raise().record_payout(PayoutEvent(
            request_id=req.id, amount=float(amount),
            payout_date=(payout_date or "").strip(),
            confirmed_by=confirmed_by, reference=reference.strip(),
            note=note.strip(), site_id=req.site_id,
        ))

    def record_deduction(
        self, request_id: int, amount: float, period: str,
        confirmed_by: str, deduction_date: str = "",
        reference: str = "", note: str = "",
        site_id: str | None = None,
    ) -> DeductionEvent:
        """Confirm a payroll-deduction event (advance requests only)."""
        req = self._get(request_id, site_id)
        if req.status != HRRequestStatus.APPROVED:
            raise DatabaseError("Deductions record only on approved requests.")
        if req.request_type != HRRequestType.ADVANCE:
            raise DatabaseError("Only advances carry payroll deductions.")
        if not _MONTH_RE.match(period or ""):
            raise DatabaseError("Deduction period must be YYYY-MM.")
        self._check_amount(amount)
        return self._money_or_raise().record_deduction(DeductionEvent(
            request_id=req.id, amount=float(amount), period=period,
            deduction_date=(deduction_date or "").strip(),
            confirmed_by=confirmed_by, reference=reference.strip(),
            note=note.strip(), site_id=req.site_id,
        ))

    def financial_status(self, request_id: int,
                         site_id: str | None = None) -> dict:
        """Derived financial state from the event ledger (never stored)."""
        req = self._get(request_id, site_id)
        if req.request_type not in (HRRequestType.ADVANCE, HRRequestType.TRANSPORT):
            # Leave/mission/overtime carry no money leg: approval closes.
            state = "open"
            if req.status == HRRequestStatus.APPROVED:
                state = "closed"
            elif req.status == HRRequestStatus.REJECTED:
                state = "rejected"
            return {"state": state, "paid": 0, "required": 0,
                    "disputed": self._is_disputed(req)}
        money = self._money_or_raise()
        paid = money.paid_total(req.id, site_id=req.site_id)
        deducted = money.deducted_total(req.id, site_id=req.site_id)
        required = float(req.amount or 0)
        disputed = self._is_disputed(req)
        if req.request_type == HRRequestType.TRANSPORT:
            state = "disputed" if disputed else (
                "closed" if paid >= required and required > 0 else (
                    "partially_paid" if paid > 0 else "pending_payout"))
            return {"state": state, "paid": paid, "required": required,
                    "disputed": disputed}
        state = "disputed" if disputed else "pending_payout"
        if paid >= required and required > 0:
            state = "paid"
        elif paid > 0:
            state = "partially_paid"
        deduction_state = "pending_deduction"
        if deducted >= required and required > 0:
            deduction_state = "fully_deducted"
        elif deducted > 0:
            deduction_state = "partially_deducted"
        if not disputed and state == "paid" and deduction_state == "fully_deducted":
            state = "closed"
        return {"state": state, "deduction_state": deduction_state,
                "paid": paid, "deducted": deducted,
                "required": required, "disputed": disputed}

    def try_close(self, request_id: int,
                  site_id: str | None = None) -> bool:
        """Close a fully reconciled request (audit signature, no flags)."""
        req = self._get(request_id, site_id)
        if self.financial_status(req.id, site_id=req.site_id)["state"] != "closed":
            return False
        _sign(req, "system", "system", "Atlas-Bot", "closed")
        self._repo.save(req)
        return True

    def dispute(self, request_id: int, chat_id: str, name: str,
                note: str, site_id: str | None = None) -> HRRequest:
        """Flag a dispute (explicit, audited; resolution clears it)."""
        req = self._get(request_id, site_id)
        if not (note or "").strip():
            raise DatabaseError("Dispute needs a note.")
        _sign(req, "dispute", chat_id, name, "disputed", note=note)
        return self._repo.save(req)

    def resolve_dispute(self, request_id: int, chat_id: str, name: str,
                        role: str, note: str = "",
                        site_id: str | None = None) -> HRRequest:
        """Clear a dispute with an authorized resolution entry."""
        req = self._get(request_id, site_id)
        if not self._is_disputed(req):
            raise DatabaseError("No open dispute on this request.")
        _sign(req, role, chat_id, name, "dispute-resolved", note=note)
        return self._repo.save(req)

    @staticmethod
    def _is_disputed(req: HRRequest) -> bool:
        open_dispute = False
        for sig in req.signatures or []:
            if sig.get("decision") == "disputed":
                open_dispute = True
            elif sig.get("decision") == "dispute-resolved":
                open_dispute = False
        return open_dispute

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
    def _check_date(value: str, label: str) -> str:
        from datetime import date as _date

        text = (value or "").strip()
        try:
            _date.fromisoformat(text)
        except ValueError:
            raise DatabaseError(f"{label} must be YYYY-MM-DD.")
        return text

    @classmethod
    def _check_range(cls, start_date: str, end_date: str) -> tuple[str, str]:
        start = cls._check_date(start_date, "Start date")
        end = cls._check_date(end_date, "End date")
        if end < start:
            raise DatabaseError("End date must not precede start date.")
        return start, end

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
