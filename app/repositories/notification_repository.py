"""Notification outbox repository (031): durable, site-scoped rows."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.database.manager import DatabaseManager
from app.models.notifications import Notification, NotificationStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _shifted(now_iso: str, minutes: int) -> str:
    from datetime import datetime, timedelta

    try:
        return (datetime.fromisoformat(now_iso)
                + timedelta(minutes=minutes)).isoformat()
    except ValueError:
        return now_iso

_COLUMNS = (
    "dedup_key, recipient, site_id, ntype, reference, text, priority,"
    " status, attempts, max_attempts, next_retry_at, last_error,"
    " created_at, sent_at, acknowledged_at, claimed_at"
)


class NotificationRepository:
    """Persistence for notification_outbox (no deletes except tests)."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    def add(self, entity: Notification) -> Notification:
        cursor = self._db.execute(
            f"""INSERT INTO notification_outbox ({_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.dedup_key, str(entity.recipient), entity.site_id,
                entity.ntype, entity.reference, entity.text, entity.priority,
                entity.status, entity.attempts, entity.max_attempts,
                entity.next_retry_at, entity.last_error,
                entity.created_at, entity.sent_at, entity.acknowledged_at,
                entity.claimed_at,
            ),
        )
        self._db.commit()
        entity.id = cursor.lastrowid
        return entity

    def get_by_dedup(self, dedup_key: str) -> Optional[Notification]:
        row = self._db.execute(
            "SELECT * FROM notification_outbox WHERE dedup_key = ?",
            (dedup_key,),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def get_by_id(self, nid: int) -> Optional[Notification]:
        row = self._db.execute(
            "SELECT * FROM notification_outbox WHERE id = ?",
            (nid,),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def claim(self, nid: int, now_iso: str,
              lease_min: int = 10) -> Optional[Notification]:
        """Atomically move pending+due -> sending (single-process lease).

        Stuck sending rows (claimed before the lease window) are
        reclaimable: a crash between claim and send can never strand
        a notification forever. Returns the row when this caller owns
        it, else None.
        """
        cursor = self._db.execute(
            """UPDATE notification_outbox SET status='sending', claimed_at=?
               WHERE id=? AND ((status='pending'
               AND (next_retry_at IS NULL OR next_retry_at <= ?))
               OR (status='sending' AND (claimed_at IS NULL
               OR claimed_at <= ?)))""",
            (now_iso, nid, now_iso, _shifted(now_iso, -lease_min)),
        )
        self._db.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_by_id(nid)

    def due_ids(self, now_iso: str, limit: int = 50,
                lease_min: int = 10) -> list[int]:
        rows = self._db.execute(
            """SELECT id FROM notification_outbox
               WHERE (status = 'pending'
                      AND (next_retry_at IS NULL OR next_retry_at <= ?))
                  OR (status = 'sending'
                      AND (claimed_at IS NULL OR claimed_at <= ?))
               ORDER BY priority DESC, created_at ASC LIMIT ?""",
            (now_iso, _shifted(now_iso, -lease_min), limit),
        ).fetchall()
        return [r["id"] for r in rows]

    def mark_sent(self, nid: int, now_iso: str) -> None:
        self._db.execute(
            "UPDATE notification_outbox SET status='sent', sent_at=? WHERE id=?",
            (now_iso, nid),
        )
        self._db.commit()

    def mark_failed(self, nid: int, terminal: bool, error: str,
                    next_retry_iso: str | None, now_iso: str) -> None:
        if terminal:
            self._db.execute(
                """UPDATE notification_outbox
                   SET status='failed', last_error=? WHERE id=?""",
                (error, nid),
            )
        else:
            self._db.execute(
                """UPDATE notification_outbox
                   SET status='pending', attempts=attempts+1,
                       next_retry_at=?, last_error=? WHERE id=?""",
                (next_retry_iso, error, nid),
            )
        self._db.commit()

    def ack(self, nid: int, now_iso: str) -> bool:
        cursor = self._db.execute(
            """UPDATE notification_outbox SET acknowledged_at=?
               WHERE id=? AND acknowledged_at IS NULL""",
            (now_iso, nid),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def cancel_for_reference(self, site_id: str, reference: str) -> int:
        """Supersede pending rows for a completed workflow object.

        Returns rows cancelled. Terminal rows are never touched.
        """
        cursor = self._db.execute(
            """UPDATE notification_outbox SET status='failed',
               last_error='superseded'
               WHERE site_id=? AND reference=? AND status='pending'""",
            (site_id, reference),
        )
        self._db.commit()
        return cursor.rowcount

    def pending_count(self, site_id: str | None = None) -> int:
        if site_id is None:
            row = self._db.execute(
                "SELECT COUNT(*) as cnt FROM notification_outbox "
                "WHERE status = 'pending'").fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) as cnt FROM notification_outbox "
                "WHERE status = 'pending' AND site_id = ?",
                (site_id,)).fetchone()
        return row["cnt"] if row else 0

    def pending_for_site(self, site_id: str, limit: int = 200) -> list[Notification]:
        rows = self._db.execute(
            """SELECT * FROM notification_outbox
               WHERE site_id = ? AND status = 'pending'
               ORDER BY priority DESC, created_at ASC LIMIT ?""",
            (site_id, limit),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    @staticmethod
    def _row_to_model(row) -> Notification:
        return Notification(
            id=row["id"],
            dedup_key=row["dedup_key"],
            recipient=str(row["recipient"]),
            site_id=row["site_id"],
            ntype=row["ntype"],
            reference=row["reference"] or "",
            text=row["text"] or "",
            priority=row["priority"] or 0,
            status=row["status"],
            attempts=row["attempts"] or 0,
            max_attempts=row["max_attempts"] or 4,
            next_retry_at=row["next_retry_at"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            sent_at=row["sent_at"],
            acknowledged_at=row["acknowledged_at"],
            claimed_at=row["claimed_at"],
        )
