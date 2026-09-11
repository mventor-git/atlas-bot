"""Payroll service (020): monthly runs per ADR-009.

Formula: net = base + ot_hours * (base / standard_hours) * multiplier
         - advances - deductions, rounded to 2dp.
Runs are editable until export; exported runs are immutable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.database import driver
from app.models.payroll import PayrollLine, PayrollRun, PayrollRunStatus
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PayrollService:
    """Monthly payroll runs (handlers gate manage_payroll)."""

    def __init__(self, payroll_repo: PayrollRepository,
                 user_repo: UserRepository) -> None:
        self._repo = payroll_repo
        self._users = user_repo

    # --- math (pure) ---

    @staticmethod
    def compute_net(base_pay: float, ot_hours: float, standard_hours: float,
                    multiplier: float, advances: float,
                    deductions: float) -> tuple[float, float]:
        """Return (ot_amount, net), rounded to 2dp. Rejects negatives."""
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

    # --- runs ---

    def create_run(self, period: str, created_by: str,
                   site_id: str | None = None, ot_multiplier: float = 1.5,
                   standard_hours: float = 240.0) -> PayrollRun:
        """Open a draft run for YYYY-MM (one draft per period+site)."""
        import re

        if not re.fullmatch(r"\d{4}-\d{2}", period or ""):
            raise DatabaseError("Period must be YYYY-MM.")
        site = site_id or driver.site_id()
        if self._repo.get_by_period(period, site_id=site) is not None:
            raise DatabaseError(f"A run already exists for {period}.")
        return self._repo.add(PayrollRun(
            period=period, site_id=site, status=PayrollRunStatus.DRAFT,
            ot_multiplier=ot_multiplier, standard_hours=standard_hours,
            created_by=created_by,
        ))

    def add_line(self, run_id: int, chat_id: str, base_pay: float,
                 ot_hours: float = 0.0, advances: float = 0.0,
                 deductions: float = 0.0,
                 site_id: str | None = None) -> PayrollLine:
        """Add a computed line to a draft run."""
        run = self._draft(run_id, site_id)
        ot_amount, net = self.compute_net(
            base_pay, ot_hours, run.standard_hours,
            run.ot_multiplier, advances, deductions)
        return self._repo.add_line(PayrollLine(
            run_id=run.id, chat_id=chat_id, base_pay=float(base_pay),
            ot_hours=float(ot_hours), ot_amount=ot_amount,
            advances=float(advances), deductions=float(deductions), net=net))

    def update_line(self, line_id: int, run_id: int, base_pay: float,
                    ot_hours: float, advances: float, deductions: float,
                    site_id: str | None = None) -> PayrollLine:
        """Recompute a line (draft runs only)."""
        run = self._draft(run_id, site_id)
        ot_amount, net = self.compute_net(
            base_pay, ot_hours, run.standard_hours,
            run.ot_multiplier, advances, deductions)
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
        """Create a run with bases pulled from the salary store.

        Each entry: {chat_id, ot_hours?, advances?, deductions?}.
        Missing salary blocks that line explicitly (never silent zero).
        """
        run = self.create_run(period, created_by, site_id,
                              ot_multiplier, standard_hours)
        for entry in entries:
            chat_id = str(entry["chat_id"])
            user = self._users.get_by_chat_id(chat_id)
            if user is None or user.monthly_salary is None:
                raise DatabaseError(
                    f"No salary stored for {chat_id}; set it before payroll.")
            self.add_line(run.id, chat_id, user.monthly_salary,
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

    # --- CSV import (chat_id,salary per line) ---

    def import_salaries(self, csv_text: str) -> dict:
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
                user = self._users.set_salary(chat_id, salary)
            except DatabaseError:
                skipped += 1
                continue
            if user is None:
                unknown += 1
            else:
                updated += 1
        return {"updated": updated, "unknown": unknown, "skipped": skipped}

    # --- internals ---

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
