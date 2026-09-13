"""Payroll service (020; 5A safety hardened): monthly runs per ADR-009.

Formula: net = base + ot_hours * (base / standard_hours) * multiplier
         - advances - deductions, rounded to 2dp.
Runs are editable until export; exported runs are immutable.
Negative net is preserved as-is (business decision deferred to 5B).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from app.database import driver
from app.models.hr import HRRequestStatus, HRRequestType
from app.models.payroll import (HoursBasis, PayrollAdjustment, PayrollLine,
                                PayrollPolicy, PayrollRun, PayrollRunStatus,
                                RoundingRule, SYSTEM_POLICY_DEFAULTS)
from app.repositories.hr_repository import HRRepository
from app.repositories.money_repository import MoneyRepository
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.attendance_day_repository import (
        AttendanceDayRepository)
    from app.repositories.membership_repository import MembershipRepository

logger = get_logger(__name__)

SOURCES = ("manual", "approved")
"""Input provenance: hand-typed (provisional) vs backed by approved records."""


class PayrollService:
    """Monthly payroll runs (handlers gate manage_payroll)."""

    def __init__(self, payroll_repo: PayrollRepository,
                 user_repo: UserRepository,
                 membership_repo: MembershipRepository | None = None,
                 hr_repo: HRRepository | None = None,
                 money_repo: MoneyRepository | None = None,
                 day_repo: AttendanceDayRepository | None = None) -> None:
        self._repo = payroll_repo
        self._users = user_repo
        self._memberships = membership_repo
        self._hr = hr_repo
        self._money = money_repo
        self._days = day_repo

    # --- math (pure) ---

    @staticmethod
    def compute_net(base_pay: float, ot_hours: float, standard_hours: float,
                    multiplier: float, advances: float,
                    deductions: float,
                    rounding: str = RoundingRule.STANDARD_2DP
                    ) -> tuple[float, float]:
        """Return (ot_amount, net). Rejects negatives.

        NOTE (5A): net itself may be negative when advances+deductions
        exceed base+OT. This is preserved, NOT clamped — flooring is a
        business-policy decision deferred to Stage 5B.
        Only 'standard_2dp' rounding exists; anything else is refused
        loudly instead of silently mapped to a made-up rule.
        """
        for name, value in (("base_pay", base_pay), ("ot_hours", ot_hours),
                            ("advances", advances), ("deductions", deductions)):
            if value is None or float(value) < 0:
                raise DatabaseError(f"{name} must be a non-negative number.")
        if standard_hours is None or float(standard_hours) <= 0:
            raise DatabaseError("standard_hours must be positive.")
        if rounding != RoundingRule.STANDARD_2DP:
            raise DatabaseError(
                f"Rounding '{rounding}' is not implemented; "
                f"only '{RoundingRule.STANDARD_2DP}' exists.")
        for name, value in (("base_pay", base_pay), ("ot_hours", ot_hours),
                            ("advances", advances), ("deductions", deductions)):
            if value is None or float(value) < 0:
                raise DatabaseError(f"{name} must be a non-negative number.")
        if standard_hours is None or float(standard_hours) <= 0:
            raise DatabaseError("standard_hours must be positive.")
        ot_amount = round(
            float(ot_hours) * (float(base_pay) / float(standard_hours))
            * float(multiplier), 2)
        net = round(float(base_pay) + ot_amount
                    - float(advances) - float(deductions), 2)
        return ot_amount, net

    # --- policy (5B: versioned, effective-dated, snapshotted) ---

    def set_policy(self, site_id: str, set_by: str,
                   standard_hours: float = 240.0,
                   ot_multiplier: float = 1.5,
                   hours_basis: str = HoursBasis.FIXED,
                   rounding: str = RoundingRule.STANDARD_2DP,
                   effective_from: str | None = None,
                   reason: str | None = None) -> PayrollPolicy:
        """Append a new policy version (never rewrites history).

        Only implemented modes are accepted; anything else is refused
        instead of silently stored. effective_from defaults to today.
        """
        if hours_basis != HoursBasis.FIXED:
            raise DatabaseError(
                f"hours_basis '{hours_basis}' is not implemented; "
                f"only '{HoursBasis.FIXED}' exists.")
        if rounding != RoundingRule.STANDARD_2DP:
            raise DatabaseError(
                f"Rounding '{rounding}' is not implemented; "
                f"only '{RoundingRule.STANDARD_2DP}' exists.")
        if float(standard_hours) <= 0:
            raise DatabaseError("standard_hours must be positive.")
        if float(ot_multiplier) < 0:
            raise DatabaseError("ot_multiplier must be non-negative.")
        eff = effective_from or datetime.now().date().isoformat()
        try:
            datetime.strptime(eff, "%Y-%m-%d")
        except ValueError:
            raise DatabaseError("effective_from must be YYYY-MM-DD.") from None
        version = self._repo.latest_policy_version(site_id) + 1
        return self._repo.add_policy(PayrollPolicy(
            site_id=site_id, version=version, effective_from=eff,
            hours_basis=hours_basis, standard_hours=float(standard_hours),
            ot_multiplier=float(ot_multiplier), rounding=rounding,
            set_by=set_by, reason=reason))

    def active_policy(self, site_id: str, period: str) -> PayrollPolicy | None:
        """Max version with effective_from <= first day of period."""
        first_day = f"{period}-01"
        best: PayrollPolicy | None = None
        for policy in self._repo.policies_for(site_id):
            if policy.effective_from <= first_day:
                if best is None or policy.version > best.version:
                    best = policy
        return best

    @staticmethod
    def _snapshot(policy: PayrollPolicy | None, origin: str) -> str:
        """Frozen calculation params stored on the run (reproducibility)."""
        if policy is None:
            params = dict(SYSTEM_POLICY_DEFAULTS)
        else:
            params = {
                "hours_basis": policy.hours_basis,
                "standard_hours": policy.standard_hours,
                "ot_multiplier": policy.ot_multiplier,
                "rounding": policy.rounding,
            }
        params["origin"] = origin
        if policy is not None:
            params["version"] = policy.version
            params["effective_from"] = policy.effective_from
        return json.dumps(params, sort_keys=True)

    # --- runs ---

    def create_run(self, period: str, created_by: str,
                   site_id: str | None = None, ot_multiplier: float = 1.5,
                   standard_hours: float = 240.0) -> PayrollRun:
        """Open a draft run for YYYY-MM (one draft per period+site).

        Params resolve from the active policy when the caller leaves the
        dev defaults untouched; explicitly passed values are recorded as
        an explicit_override snapshot. Either way the frozen params live
        on the run, so later policy changes cannot rewrite history.
        """
        import re

        match = re.fullmatch(r"(\d{4})-(\d{2})", period or "")
        if not match:
            raise DatabaseError("Period must be YYYY-MM.")
        if not 1 <= int(match.group(2)) <= 12:
            raise DatabaseError("Period month must be 01-12.")
        site = site_id or driver.site_id()
        if self._repo.get_by_period(period, site_id=site) is not None:
            raise DatabaseError(f"A run already exists for {period}.")
        policy = self.active_policy(site, period)
        if (ot_multiplier == 1.5 and standard_hours == 240.0
                and policy is not None):
            ot_multiplier = policy.ot_multiplier
            standard_hours = policy.standard_hours
            snapshot = self._snapshot(policy, origin="policy")
            policy_version = policy.version
        elif policy is None:
            snapshot = self._snapshot(None, origin="system_default")
            policy_version = None
        else:
            snapshot = self._snapshot(policy, origin="explicit_override")
            policy_version = None
        return self._repo.add(PayrollRun(
            period=period, site_id=site, status=PayrollRunStatus.DRAFT,
            ot_multiplier=ot_multiplier, standard_hours=standard_hours,
            created_by=created_by, policy_version=policy_version,
            policy_snapshot=snapshot,
        ))

    def add_line(self, run_id: int, chat_id: str, base_pay: float,
                 ot_hours: float = 0.0, advances: float = 0.0,
                 deductions: float = 0.0,
                 site_id: str | None = None,
                 ot_source: str = "manual", ot_refs: str | None = None,
                 advances_source: str = "manual",
                 advances_refs: str | None = None,
                 deductions_source: str = "manual",
                 deductions_refs: str | None = None) -> PayrollLine:
        """Add a computed line to a draft run.

        Single funnel for every line-creation path: enforces draft state,
        subject/site membership, one-line-per-employee, and salary
        provenance before touching the DB (UNIQUE constraint backs it).
        Sources default to 'manual' (provisional); pass 'approved' with
        refs (from approved_ot_for / period_deductions_for) to cite
        authoritative records. 'approved' without refs is refused.
        """
        run = self._draft(run_id, site_id)
        self._check_subject(run.site_id, chat_id)
        if any(str(existing.chat_id) == str(chat_id)
               for existing in self._repo.lines_for(run.id)):
            raise DatabaseError(
                f"Run {run.id} already has a line for {chat_id}; "
                "duplicate payroll lines are refused.")
        for name, source, refs in (
                ("ot", ot_source, ot_refs),
                ("advances", advances_source, advances_refs),
                ("deductions", deductions_source, deductions_refs)):
            if source not in SOURCES:
                raise DatabaseError(
                    f"Unknown {name} source '{source}'; "
                    f"expected one of {SOURCES}.")
            if source == "approved" and not refs:
                raise DatabaseError(
                    f"'approved' {name} input must cite record refs.")
        history = self._users.salary_for_period(str(chat_id), run.period)
        if history is None:
            raise DatabaseError(
                f"No salary effective for {chat_id} in {run.period}; "
                "record a salary with an effective date on/before the "
                "period so the line can cite its provenance.")
        snapshot = json.loads(run.policy_snapshot or "{}")
        ot_amount, net = self.compute_net(
            base_pay, ot_hours, run.standard_hours,
            run.ot_multiplier, advances, deductions,
            rounding=snapshot.get("rounding", RoundingRule.STANDARD_2DP))
        return self._repo.add_line(PayrollLine(
            run_id=run.id, chat_id=chat_id, base_pay=float(base_pay),
            ot_hours=float(ot_hours), ot_amount=ot_amount,
            advances=float(advances), deductions=float(deductions),
            net=net, salary_history_id=history["id"],
            ot_source=ot_source, ot_refs=ot_refs,
            advances_source=advances_source, advances_refs=advances_refs,
            deductions_source=deductions_source,
            deductions_refs=deductions_refs))

    def update_line(self, line_id: int, run_id: int, base_pay: float,
                    ot_hours: float, advances: float, deductions: float,
                    site_id: str | None = None) -> PayrollLine:
        """Recompute a line (draft runs only)."""
        run = self._draft(run_id, site_id)
        snapshot = json.loads(run.policy_snapshot or "{}")
        ot_amount, net = self.compute_net(
            base_pay, ot_hours, run.standard_hours,
            run.ot_multiplier, advances, deductions,
            rounding=snapshot.get("rounding", RoundingRule.STANDARD_2DP))
        lines = {line.id: line for line in self._repo.lines_for(run.id)}
        if line_id not in lines:
            raise DatabaseError(f"Line {line_id} not in run {run_id}.")
        line = lines[line_id]
        line.base_pay, line.ot_hours = float(base_pay), float(ot_hours)
        line.ot_amount, line.advances = ot_amount, float(advances)
        line.deductions, line.net = float(deductions), net
        return self._repo.update_line(line)

    def build_run(self, period: str, created_by: str,
                  entries: list[dict], site_id: str | None = None,
                  ot_multiplier: float = 1.5,
                  standard_hours: float = 240.0) -> PayrollRun:
        """Create a run with bases pulled from period-effective salaries.

        Each entry: {chat_id, ot_hours?, advances?, deductions?}.
        A subject with no salary effective for the period blocks that
        line explicitly (never silent zero, never today's cache value).
        """
        run = self.create_run(period, created_by, site_id,
                              ot_multiplier, standard_hours)
        for entry in entries:
            chat_id = str(entry["chat_id"])
            salary = self._users.salary_for_period(chat_id, period)
            if salary is None:
                raise DatabaseError(
                    f"No salary effective for {chat_id} in {period}; "
                    "record one before payroll.")
            self.add_line(run.id, chat_id, salary["amount"],
                          entry.get("ot_hours", 0.0),
                          entry.get("advances", 0.0),
                          entry.get("deductions", 0.0),
                          site_id=run.site_id)
        return run

    def mark_exported(self, run_id: int,
                      site_id: str | None = None) -> PayrollRun:
        """Lock a run at PDF export (ADR-009)."""
        run = self._get(run_id, site_id)
        if run.status != PayrollRunStatus.DRAFT:
            raise DatabaseError(f"Run {run_id} is {run.status}, cannot export.")
        run.status = PayrollRunStatus.EXPORTED
        run.exported_at = datetime.now().isoformat()
        return self._repo.update(run)

    # --- approved inputs (5B suggesters; application stays explicit) ---

    def _subject_sites(self, subject: str, site_id: str) -> list[str]:
        """Sites to scan for a subject's approved records."""
        if self._memberships is not None:
            return [m.site_id
                    for m in self._memberships.active_for_user(subject)]
        return [site_id]

    def approved_ot_for(self, chat_id: str, site_id: str,
                        period: str) -> dict:
        """Approved overtime backing a payroll input (suggest, not apply).

        Sums APPROVED hr_requests overtime whose day falls in period.
        pm_confirmed/pending/rejected never count. Returns
        {hours, request_ids, by_site} for the officer to confirm.
        """
        if self._hr is None:
            raise DatabaseError("HR repository is not wired.")
        subject = str(chat_id)
        total, ids, by_site = 0.0, [], {}
        for site in self._subject_sites(subject, site_id):
            for req in self._hr.list_for_requester(subject, site_id=site):
                if (req.request_type == HRRequestType.OVERTIME
                        and req.status == HRRequestStatus.APPROVED
                        and (req.start_date or "")[:7] == period):
                    total += float(req.hours or 0.0)
                    ids.append(req.id)
                    by_site[site] = by_site.get(site, 0.0) + float(req.hours or 0.0)
        return {"hours": round(total, 2), "request_ids": ids,
                "by_site": by_site}

    def period_deductions_for(self, chat_id: str, site_id: str,
                              period: str) -> dict:
        """Recorded deduction events backing a payroll input (suggest).

        Only confirmed (recorded) deduction_events count; requested or
        approved-but-unrecorded advances never count. Returns
        {total, event_ids} for the officer to confirm.
        """
        if self._money is None:
            raise DatabaseError("Money repository is not wired.")
        events = self._money.deductions_for_subject(
            str(chat_id), period, site_id=site_id)
        return {"total": round(sum(e.amount for e in events), 2),
                "event_ids": [e.id for e in events]}

    def salary_for_period(self, chat_id: str, period: str) -> dict | None:
        """Salary row applicable to a period (5C rule B passthrough)."""
        return self._users.salary_for_period(str(chat_id), period)

    def list_runs(self, site_id: str | None = None) -> list[PayrollRun]:
        """Runs of one site, newest period first (HQ inspection)."""
        return self._repo.get_all(site_id=site_id)

    def attendance_summary_for(self, chat_id: str, site_id: str,
                               period: str) -> dict:
        """Read-only finalized-attendance signal (5C; zero payroll effect).

        Counts RESOLVED attendance days in the period by verdict.
        Pending/confirmed/disputed days are reported separately and must
        never drive payroll. No monetary mapping exists (business OPEN);
        manual confirmation remains authoritative.
        """
        if self._days is None:
            raise DatabaseError("Attendance repository is not wired.")
        resolved: dict[str, int] = {}
        open_count = 0
        for day in self._days.days_for_chat(str(chat_id), site_id=site_id):
            if not str(day.day_date or "").startswith(period):
                continue
            if day.status == "resolved":
                verdict = day.verdict or "unverdict"
                resolved[verdict] = resolved.get(verdict, 0) + 1
            else:
                open_count += 1
        return {"resolved_by_verdict": resolved,
                "open_days": open_count}

    # --- explanation (5B foundation for "why is my net X?") ---

    def explain_line(self, run_id: int, line_id: int,
                     site_id: str | None = None) -> dict:
        """Structured breakdown of one line for authorized HR."""
        run = self._get(run_id, site_id)
        lines = {line.id: line for line in self._repo.lines_for(run.id)}
        if line_id not in lines:
            raise DatabaseError(f"Line {line_id} not in run {run_id}.")
        line = lines[line_id]
        snapshot = json.loads(run.policy_snapshot or "{}")
        adjustments = self._repo.adjustments_for(run.id, line.chat_id)
        adj_total = round(sum(a.amount for a in adjustments), 2)
        hourly = (round(float(line.base_pay) / float(run.standard_hours), 4)
                  if run.standard_hours else 0.0)
        return {
            "period": run.period, "site_id": run.site_id,
            "status": run.status, "chat_id": line.chat_id,
            "base_pay": line.base_pay,
            "salary_history_id": line.salary_history_id,
            "hours_basis": snapshot.get(
                "hours_basis", SYSTEM_POLICY_DEFAULTS["hours_basis"]),
            "standard_hours": run.standard_hours,
            "hourly_rate": hourly,
            "ot_hours": line.ot_hours, "ot_source": line.ot_source,
            "ot_refs": line.ot_refs, "ot_multiplier": run.ot_multiplier,
            "ot_amount": line.ot_amount,
            "advances": line.advances,
            "advances_source": line.advances_source,
            "advances_refs": line.advances_refs,
            "deductions": line.deductions,
            "deductions_source": line.deductions_source,
            "deductions_refs": line.deductions_refs,
            "rounding": snapshot.get(
                "rounding", SYSTEM_POLICY_DEFAULTS["rounding"]),
            "policy_origin": snapshot.get("origin", "unknown"),
            "policy_version": run.policy_version,
            "net": line.net,
            "negative_net_review": line.net < 0,
            "attendance": self._safe_attendance(line.chat_id, run.site_id,
                                                run.period),
            "adjustments": [
                {"amount": a.amount, "reason": a.reason,
                 "created_by": a.created_by, "created_at": a.created_at}
                for a in adjustments],
            "adjustments_total": adj_total,
            "adjusted_net": round(line.net + adj_total, 2),
        }

    # --- corrections (5B: adjustments on exported runs, never UPDATE) ---

    def _safe_attendance(self, chat_id: str, site_id: str,
                         period: str) -> dict:
        """Attendance signal for explain; unwired repo reads as unknown."""
        if self._days is None:
            return {"resolved_by_verdict": {}, "open_days": "unknown"}
        return self.attendance_summary_for(chat_id, site_id, period)

    def add_adjustment(self, run_id: int, chat_id: str, amount: float,
                       reason: str, created_by: str,
                       site_id: str | None = None) -> PayrollAdjustment:
        """Record an audited correction on an EXPORTED run.

        Draft runs must edit lines instead. Adjustments never mutate the
        locked line; adjusted_net = net + sum(adjustments).
        """
        run = self._get(run_id, site_id)
        if run.status != PayrollRunStatus.EXPORTED:
            raise DatabaseError(
                f"Run {run_id} is {run.status}; corrections apply to "
                "exported runs only (edit draft lines instead).")
        if not (reason or "").strip():
            raise DatabaseError("A correction reason is required.")
        self._check_subject(run.site_id, chat_id)
        lines = {str(line.chat_id): line
                 for line in self._repo.lines_for(run.id)}
        if str(chat_id) not in lines:
            raise DatabaseError(
                f"No line for {chat_id} in run {run_id}.")
        return self._repo.add_adjustment(PayrollAdjustment(
            run_id=run.id, chat_id=str(chat_id), amount=float(amount),
            reason=reason.strip(), created_by=created_by))

    def adjustments_for(self, run_id: int, chat_id: str | None = None,
                        site_id: str | None = None) -> list[PayrollAdjustment]:
        run = self._get(run_id, site_id)
        return self._repo.adjustments_for(run.id, chat_id)

    def adjusted_net(self, run_id: int, chat_id: str,
                     site_id: str | None = None) -> float:
        run = self._get(run_id, site_id)
        lines = [line for line in self._repo.lines_for(run.id)
                 if str(line.chat_id) == str(chat_id)]
        if not lines:
            raise DatabaseError(f"No line for {chat_id} in run {run_id}.")
        total = sum(a.amount for a in
                    self._repo.adjustments_for(run.id, chat_id))
        return round(lines[0].net + total, 2)

    # --- Queries (public for handlers; Phase 1 decoupling) ---

    def get_run(self, period: str, site_id: str | None = None) -> Optional[PayrollRun]:
        return self._repo.get_by_period(period, site_id=site_id)

    def lines(self, run) -> list:
        """Lines of a run already loaded through a site check."""
        return self._repo.lines_for(run.id)

    def user_salary(self, chat_id: str) -> Optional[float]:
        user = self._users.get_by_chat_id(chat_id)
        return user.monthly_salary if user else None

    def set_salary(self, chat_id: str, amount: float,
                   set_by: str | None = None,
                   reason: str | None = None):
        return self._users.set_salary(chat_id, amount, set_by=set_by,
                                      reason=reason)

    # --- CSV import (chat_id,salary per line) ---

    def import_salaries(self, csv_text: str,
                        set_by: str | None = None) -> dict:
        """Set salaries from CSV text. Returns {updated, unknown, skipped}."""
        updated, unknown, skipped = 0, 0, 0
        for raw in (csv_text or "").strip().splitlines():
            line = raw.strip()
            if not line:
                continue
            cells = [c.strip() for c in line.split(",")]
            if len(cells) != 2 or cells[0].lower() == "chat_id":
                skipped += 1
                continue
            chat_id, amount = cells
            try:
                salary = float(amount)
            except ValueError:
                skipped += 1
                continue
            try:
                user = self._users.set_salary(chat_id, salary, set_by=set_by,
                                              reason="csv import")
            except DatabaseError:
                skipped += 1
                continue
            if user is None:
                unknown += 1
            else:
                updated += 1
        return {"updated": updated, "unknown": unknown, "skipped": skipped}

    # --- internals ---

    def _check_subject(self, site_id: str | None, subject_chat_id: str) -> None:
        """Refuse cross-site line injection (5A).

        The subject must hold an ACTIVE membership at the run's site.
        Without a membership repo (legacy/dev wiring) fall back to the
        legacy users.site_id signal when present; data with no site
        signal at all is allowed through as before.
        """
        subject = str(subject_chat_id)
        if self._memberships is not None:
            sites = [m.site_id
                     for m in self._memberships.active_for_user(subject)]
            if site_id not in sites:
                raise DatabaseError(
                    f"User {subject} has no active membership at "
                    f"`{site_id}`; cannot add them to this site's payroll.")
            return
        user = self._users.get_by_chat_id(subject)
        if user is not None and user.site_id and user.site_id != site_id:
            raise DatabaseError(
                f"User {subject} belongs to `{user.site_id}`, not "
                f"`{site_id}`; cannot add them to this site's payroll.")

    def _get(self, run_id: int, site_id: str | None) -> PayrollRun:
        run = self._repo.get_by_id(run_id, site_id=site_id or driver.site_id())
        if run is None:
            raise DatabaseError(f"Payroll run {run_id} not found in this site.")
        return run

    def _draft(self, run_id: int, site_id: str | None) -> PayrollRun:
        run = self._get(run_id, site_id)
        if run.status != PayrollRunStatus.DRAFT:
            raise DatabaseError(f"Run {run_id} is exported and immutable.")
        return run
