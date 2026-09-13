"""Payroll repository (020): site-scoped runs and lines."""

from __future__ import annotations

from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.payroll import (PayrollAdjustment, PayrollLine, PayrollPolicy,
                                PayrollRun, PayrollRunStatus)
from app.repositories.base import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PayrollRepository(BaseRepository[PayrollRun]):
    """Persistence for payroll_runs + payroll_lines."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    # --- runs (BaseRepository surface) ---

    def add(self, entity: PayrollRun) -> PayrollRun:
        site = entity.site_id or driver.site_id()
        entity.site_id = site
        cursor = self._db.execute(
            """INSERT INTO payroll_runs
               (site_id, period, status, ot_multiplier, standard_hours,
                created_by, created_at, exported_at,
                policy_version, policy_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (site, entity.period, entity.status, entity.ot_multiplier,
             entity.standard_hours, entity.created_by, entity.created_at,
             entity.exported_at, entity.policy_version,
             entity.policy_snapshot),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_by_id(self, entity_id: int, site_id: str | None = None) -> Optional[PayrollRun]:
        row = self._db.execute(
            "SELECT * FROM payroll_runs WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def get_by_period(self, period: str, site_id: str | None = None) -> Optional[PayrollRun]:
        row = self._db.execute(
            "SELECT * FROM payroll_runs WHERE period = ? AND site_id = ?",
            (period, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def get_all(self, site_id: str | None = None) -> list[PayrollRun]:
        rows = self._db.execute(
            "SELECT * FROM payroll_runs WHERE site_id = ? ORDER BY period DESC",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def update(self, entity: PayrollRun) -> PayrollRun:
        if entity.id is None:
            raise ValueError("Cannot update a run without an ID.")
        cursor = self._db.execute(
            """UPDATE payroll_runs SET status=?, exported_at=?
               WHERE id=? AND site_id=?""",
            (entity.status, entity.exported_at, entity.id,
             entity.site_id or driver.site_id()),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Payroll run {entity.id} not found in this site.")
        return entity

    def delete(self, entity_id: int, site_id: str | None = None) -> bool:
        """Delete a draft run and its lines. Refuses locked (exported) runs."""
        run = self.get_by_id(entity_id, site_id=site_id)
        if run is None:
            return False
        if run.status != PayrollRunStatus.DRAFT:
            raise ValueError(
                f"Refuse to delete {run.status} payroll run {entity_id}: "
                "locked payroll is immutable (5A safety).")
        site = site_id or driver.site_id()
        self._db.execute("DELETE FROM payroll_lines WHERE run_id = ?", (entity_id,))
        cursor = self._db.execute(
            "DELETE FROM payroll_runs WHERE id = ? AND site_id = ?",
            (entity_id, site),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def count(self, site_id: str | None = None) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM payroll_runs WHERE site_id = ?",
            (site_id or driver.site_id(),),
        ).fetchone()
        return row["cnt"] if row else 0

    # --- lines ---

    def add_line(self, line: PayrollLine) -> PayrollLine:
        cursor = self._db.execute(
            """INSERT INTO payroll_lines
               (run_id, chat_id, base_pay, ot_hours, ot_amount,
                advances, deductions, net, salary_history_id,
                ot_source, ot_refs, advances_source, advances_refs,
                deductions_source, deductions_refs)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (line.run_id, line.chat_id, line.base_pay, line.ot_hours,
             line.ot_amount, line.advances, line.deductions, line.net,
             line.salary_history_id, line.ot_source, line.ot_refs,
             line.advances_source, line.advances_refs,
             line.deductions_source, line.deductions_refs),
        )
        self._db.commit()
        line.id = cursor.lastrowid
        return line

    def lines_for(self, run_id: int) -> list[PayrollLine]:
        rows = self._db.execute(
            "SELECT * FROM payroll_lines WHERE run_id = ? ORDER BY chat_id ASC",
            (run_id,),
        ).fetchall()
        return [self._row_to_line(r) for r in rows]

    def update_line(self, line: PayrollLine) -> PayrollLine:
        if line.id is None:
            raise ValueError("Cannot update a line without an ID.")
        cursor = self._db.execute(
            """UPDATE payroll_lines
               SET base_pay=?, ot_hours=?, ot_amount=?,
                   advances=?, deductions=?, net=?
               WHERE id=? AND run_id=?""",
            (line.base_pay, line.ot_hours, line.ot_amount, line.advances,
             line.deductions, line.net, line.id, line.run_id),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Payroll line {line.id} not found.")
        return line

    # --- policies (5B: versioned, effective-dated, per site) ---

    def add_policy(self, policy: PayrollPolicy) -> PayrollPolicy:
        site = policy.site_id or driver.site_id()
        policy.site_id = site
        cursor = self._db.execute(
            """INSERT INTO payroll_policies
               (site_id, version, effective_from, hours_basis,
                standard_hours, ot_multiplier, rounding,
                set_by, set_at, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (site, policy.version, policy.effective_from,
             policy.hours_basis, policy.standard_hours,
             policy.ot_multiplier, policy.rounding,
             policy.set_by, policy.set_at, policy.reason),
        )
        self._db.commit()
        policy.id = cursor.lastrowid
        return policy

    def policies_for(self, site_id: str | None = None) -> list[PayrollPolicy]:
        rows = self._db.execute(
            """SELECT * FROM payroll_policies WHERE site_id = ?
               ORDER BY version ASC""",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_policy(r) for r in rows]

    def latest_policy_version(self, site_id: str | None = None) -> int:
        row = self._db.execute(
            "SELECT MAX(version) AS v FROM payroll_policies WHERE site_id = ?",
            (site_id or driver.site_id(),),
        ).fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    # --- adjustments (5B: audited corrections on exported runs) ---

    def add_adjustment(self, adj: PayrollAdjustment) -> PayrollAdjustment:
        cursor = self._db.execute(
            """INSERT INTO payroll_adjustments
               (run_id, chat_id, amount, reason, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (adj.run_id, adj.chat_id, adj.amount, adj.reason,
             adj.created_by, adj.created_at),
        )
        self._db.commit()
        adj.id = cursor.lastrowid
        return adj

    def adjustments_for(self, run_id: int,
                        chat_id: str | None = None) -> list[PayrollAdjustment]:
        if chat_id is None:
            rows = self._db.execute(
                """SELECT * FROM payroll_adjustments WHERE run_id = ?
                   ORDER BY id ASC""",
                (run_id,),
            ).fetchall()
        else:
            rows = self._db.execute(
                """SELECT * FROM payroll_adjustments
                   WHERE run_id = ? AND chat_id = ? ORDER BY id ASC""",
                (run_id, str(chat_id)),
            ).fetchall()
        return [self._row_to_adjustment(r) for r in rows]

    # --- mapping ---

    @staticmethod
    def _row_to_run(row) -> PayrollRun:
        keys = row.keys() if hasattr(row, "keys") else []
        return PayrollRun(
            id=row["id"],
            site_id=row["site_id"] or "default" if "site_id" in keys else "default",
            period=row["period"],
            status=row["status"],
            ot_multiplier=row["ot_multiplier"],
            standard_hours=row["standard_hours"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            exported_at=row["exported_at"],
            policy_version=(row["policy_version"]
                            if "policy_version" in keys else None),
            policy_snapshot=(row["policy_snapshot"]
                             if "policy_snapshot" in keys else None),
        )

    @staticmethod
    def _row_to_line(row) -> PayrollLine:
        keys = row.keys() if hasattr(row, "keys") else []
        return PayrollLine(
            id=row["id"],
            run_id=row["run_id"],
            chat_id=str(row["chat_id"]),
            base_pay=row["base_pay"],
            ot_hours=row["ot_hours"],
            ot_amount=row["ot_amount"],
            advances=row["advances"],
            deductions=row["deductions"],
            net=row["net"],
            salary_history_id=(row["salary_history_id"]
                               if "salary_history_id" in keys else None),
            ot_source=(row["ot_source"] if "ot_source" in keys
                       and row["ot_source"] else "manual"),
            ot_refs=row["ot_refs"] if "ot_refs" in keys else None,
            advances_source=(row["advances_source"]
                             if "advances_source" in keys
                             and row["advances_source"] else "manual"),
            advances_refs=(row["advances_refs"]
                           if "advances_refs" in keys else None),
            deductions_source=(row["deductions_source"]
                               if "deductions_source" in keys
                               and row["deductions_source"] else "manual"),
            deductions_refs=(row["deductions_refs"]
                             if "deductions_refs" in keys else None),
        )

    @staticmethod
    def _row_to_policy(row) -> PayrollPolicy:
        return PayrollPolicy(
            id=row["id"],
            site_id=row["site_id"],
            version=row["version"],
            effective_from=row["effective_from"],
            hours_basis=row["hours_basis"],
            standard_hours=row["standard_hours"],
            ot_multiplier=row["ot_multiplier"],
            rounding=row["rounding"],
            set_by=row["set_by"],
            set_at=row["set_at"],
            reason=row["reason"],
        )

    @staticmethod
    def _row_to_adjustment(row) -> PayrollAdjustment:
        return PayrollAdjustment(
            id=row["id"],
            run_id=row["run_id"],
            chat_id=str(row["chat_id"]),
            amount=row["amount"],
            reason=row["reason"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )
