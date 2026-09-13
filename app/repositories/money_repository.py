"""Money-event repositories (009 ledger): payouts + deductions, append-only."""

from __future__ import annotations

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.hr import DeductionEvent, PayoutEvent
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MoneyRepository:
    """Persistence for payout_events and deduction_events (site-scoped)."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def record_payout(self, event: PayoutEvent) -> PayoutEvent:
        site = event.site_id or driver.site_id()
        event.site_id = site
        cursor = self._db.execute(
            """INSERT INTO payout_events
               (request_id, site_id, amount, payout_date, confirmed_by,
                reference, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.request_id, site, event.amount, event.payout_date,
                event.confirmed_by, event.reference, event.note,
                event.created_at,
            ),
        )
        self._db.commit()
        event.id = cursor.lastrowid
        return event

    def record_deduction(self, event: DeductionEvent) -> DeductionEvent:
        site = event.site_id or driver.site_id()
        event.site_id = site
        cursor = self._db.execute(
            """INSERT INTO deduction_events
               (request_id, site_id, amount, period, deduction_date,
                confirmed_by, reference, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.request_id, site, event.amount, event.period,
                event.deduction_date, event.confirmed_by, event.reference,
                event.note, event.created_at,
            ),
        )
        self._db.commit()
        event.id = cursor.lastrowid
        return event

    def payouts_for(self, request_id: int, site_id: str | None = None) -> list[PayoutEvent]:
        rows = self._db.execute(
            """SELECT * FROM payout_events
               WHERE request_id = ? AND site_id = ? ORDER BY id ASC""",
            (request_id, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_payout(r) for r in rows]

    def deductions_for(self, request_id: int, site_id: str | None = None) -> list[DeductionEvent]:
        rows = self._db.execute(
            """SELECT * FROM deduction_events
               WHERE request_id = ? AND site_id = ? ORDER BY id ASC""",
            (request_id, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_model_deduction(r) for r in rows]

    def paid_total(self, request_id: int, site_id: str | None = None) -> float:
        return sum(e.amount for e in self.payouts_for(request_id, site_id))

    def deducted_total(self, request_id: int, site_id: str | None = None) -> float:
        return sum(e.amount for e in self.deductions_for(request_id, site_id))

    def deductions_for_subject(self, chat_id: str, period: str,
                               site_id: str | None = None
                               ) -> list[DeductionEvent]:
        """Recorded deduction events for one employee in one YYYY-MM period.

        Joins the parent advance request for subject attribution; only
        confirmed (recorded) events are consumable by payroll (5B).
        """
        rows = self._db.execute(
            """SELECT de.* FROM deduction_events de
               JOIN hr_requests hr ON hr.id = de.request_id
               WHERE de.period = ? AND de.site_id = ?
                 AND hr.requester_chat_id = ?
               ORDER BY de.id ASC""",
            (period, site_id or driver.site_id(), str(chat_id)),
        ).fetchall()
        return [self._row_to_model_deduction(r) for r in rows]

    @staticmethod
    def _row_to_payout(row) -> PayoutEvent:
        return PayoutEvent(
            id=row["id"], request_id=row["request_id"],
            amount=row["amount"], payout_date=row["payout_date"],
            confirmed_by=row["confirmed_by"],
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            reference=row["reference"], note=row["note"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_model_deduction(row) -> DeductionEvent:
        return DeductionEvent(
            id=row["id"], request_id=row["request_id"],
            amount=row["amount"], period=row["period"],
            confirmed_by=row["confirmed_by"],
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            deduction_date=row["deduction_date"],
            reference=row["reference"], note=row["note"],
            created_at=row["created_at"],
        )
