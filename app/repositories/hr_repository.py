"""HR requests repository (advance + transport, site-scoped)."""

from __future__ import annotations

import json
from typing import Optional

from app.database import driver
from app.database.manager import DatabaseManager
from app.models.hr import HRRequest
from app.repositories.base import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

_COLUMNS = (
    "requester_chat_id, requester_name, request_type, amount, reason,"
    " site_id, trip_date, report_ref, receipt_path, deduction_month,"
    " status, assigned_to, delegated, note, signatures, pdf_path,"
    " created_at, updated_at"
)


class HRRepository(BaseRepository[HRRequest]):
    """Persistence for HR requests (tenant-scoped reads/writes)."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def add(self, entity: HRRequest) -> HRRequest:
        site = entity.site_id or driver.site_id()
        entity.site_id = site
        cursor = self._db.execute(
            f"""INSERT INTO hr_requests ({_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.requester_chat_id,
                entity.requester_name,
                entity.request_type,
                entity.amount,
                entity.reason,
                site,
                entity.trip_date,
                entity.report_ref,
                entity.receipt_path,
                entity.deduction_month,
                entity.status,
                entity.assigned_to,
                1 if entity.delegated else 0,
                entity.note,
                json.dumps(entity.signatures or [], ensure_ascii=False),
                entity.pdf_path,
                entity.created_at,
                entity.updated_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_by_id(self, entity_id: int, site_id: str | None = None) -> Optional[HRRequest]:
        row = self._db.execute(
            "SELECT * FROM hr_requests WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def list_for_requester(
        self, chat_id: str, site_id: str | None = None
    ) -> list[HRRequest]:
        rows = self._db.execute(
            """SELECT * FROM hr_requests
               WHERE requester_chat_id = ? AND site_id = ?
               ORDER BY created_at DESC""",
            (chat_id, site_id or driver.site_id()),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def list_pending(self, site_id: str | None = None) -> list[HRRequest]:
        """Queue for approvers: pending + PM-confirmed, this site."""
        rows = self._db.execute(
            """SELECT * FROM hr_requests
               WHERE site_id = ? AND status IN ('pending', 'pm_confirmed')
               ORDER BY created_at ASC""",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def list_by_status(
        self, status: str, site_id: str | None = None
    ) -> list[HRRequest]:
        rows = self._db.execute(
            """SELECT * FROM hr_requests
               WHERE site_id = ? AND status = ?
               ORDER BY created_at DESC""",
            (site_id or driver.site_id(), status),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def get_all(self, site_id: str | None = None) -> list[HRRequest]:
        rows = self._db.execute(
            "SELECT * FROM hr_requests WHERE site_id = ? ORDER BY created_at DESC",
            (site_id or driver.site_id(),),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def update(self, entity: HRRequest) -> HRRequest:
        """Update a request (same guards as save)."""
        return self.save(entity)

    def count(self, site_id: str | None = None) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM hr_requests WHERE site_id = ?",
            (site_id or driver.site_id(),),
        ).fetchone()
        return row["cnt"] if row else 0

    def save(self, entity: HRRequest) -> HRRequest:
        """Persist chain-state changes (guarded by id + site)."""
        if entity.id is None:
            raise ValueError("Cannot save an HRRequest without an ID.")
        from datetime import datetime

        entity.updated_at = datetime.now().isoformat()
        cursor = self._db.execute(
            """UPDATE hr_requests
               SET status=?, assigned_to=?, delegated=?, note=?,
                   signatures=?, pdf_path=?, deduction_month=?, updated_at=?
               WHERE id=? AND site_id=?""",
            (
                entity.status,
                entity.assigned_to,
                1 if entity.delegated else 0,
                entity.note,
                json.dumps(entity.signatures or [], ensure_ascii=False),
                entity.pdf_path,
                entity.deduction_month,
                entity.updated_at,
                entity.id,
                entity.site_id or driver.site_id(),
            ),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"HRRequest {entity.id} not found in this site.")
        return entity

    def delete(self, entity_id: int, site_id: str | None = None) -> bool:
        cursor = self._db.execute(
            "DELETE FROM hr_requests WHERE id = ? AND site_id = ?",
            (entity_id, site_id or driver.site_id()),
        )
        self._db.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_model(row) -> HRRequest:
        raw_signatures = row["signatures"] if "signatures" in row.keys() else "[]"
        try:
            signatures = json.loads(raw_signatures or "[]")
        except (TypeError, ValueError):
            signatures = []
        return HRRequest(
            id=row["id"],
            requester_chat_id=row["requester_chat_id"],
            requester_name=row["requester_name"],
            request_type=row["request_type"],
            amount=row["amount"],
            reason=row["reason"],
            site_id=row["site_id"] or "default" if "site_id" in row.keys() else "default",
            trip_date=row["trip_date"],
            report_ref=row["report_ref"],
            receipt_path=row["receipt_path"],
            deduction_month=row["deduction_month"],
            status=row["status"],
            assigned_to=row["assigned_to"],
            delegated=bool(row["delegated"]),
            note=row["note"],
            signatures=signatures,
            pdf_path=row["pdf_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
