"""Payroll repository (020): site-scoped runs and lines."""

from __future__ import annotations

from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.payroll import PayrollLine, PayrollRun
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
                created_by, created_at, exported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (site, entity.period, entity.status, entity.ot_multiplier,
             entity.standard_hours, entity.created_by, entity.created_at,
             entity.exported_at),
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
                advances, deductions, net)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (line.run_id, line.chat_id, line.base_pay, line.ot_hours,
             line.ot_amount, line.advances, line.deductions, line.net),
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

    # --- mapping ---

    @staticmethod
    def _row_to_run(row) -> PayrollRun:
        return PayrollRun(
            id=row["id"],
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            period=row["period"],
            status=row["status"],
            ot_multiplier=row["ot_multiplier"],
            standard_hours=row["standard_hours"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            exported_at=row["exported_at"],
        )

    @staticmethod
    def _row_to_line(row) -> PayrollLine:
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
        )
