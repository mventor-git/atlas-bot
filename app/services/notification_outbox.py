"""Durable notification outbox (031, Stage 4).

Enqueue is idempotent (deterministic dedup keys); dispatch claims rows
atomically, re-checks eligibility, classifies failures, and backs off.
Restart-safe: pending rows survive; sent rows never resend; failed rows
retry per policy, then terminate.

Time source: callers pass ISO timestamps (manager uses tz-aware _now()).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from app.models.notifications import Notification, NotificationStatus
from app.repositories.notification_repository import NotificationRepository
from app.services import notification_templates as templates
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_MIN = (1, 15, 60)  # after attempt 1, 2, 3


def dedup_key(site: str, date: str, recipient: str, ntype: str,
              ref: str = "", level: int = 0) -> str:
    """Deterministic identity: site|date|recipient|type|object|level."""
    return "|".join(str(p) for p in
                    (site, date, recipient, ntype, ref or "", level))


def _now_iso() -> str:
    return datetime.now().isoformat()


def classify_error(exc: Exception) -> tuple[str, bool]:
    """(category, terminal?). Telegram failures classified by type name.

    Permanent: blocked/Forbidden, BadRequest, chat gone. Everything else
    (timeouts, network, flood-wait) retries with backoff.
    """
    name = type(exc).__name__
    text = str(exc)
    permanent_markers = ("Forbidden", "BadRequest", "ChatNotFound",
                         "not found", "blocked", "deactivated", "kicked")
    if name in ("Forbidden", "BadRequest") or \
            any(m.lower() in text.lower() for m in permanent_markers):
        return f"permanent:{name}", True
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        return f"flood-wait:{name}", False
    return name or "send-failed", False


class NotificationOutbox:
    """Enqueue + dispatch over the durable outbox table."""

    def __init__(self, repo: NotificationRepository,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 backoff_min: tuple = DEFAULT_BACKOFF_MIN) -> None:
        self._repo = repo
        self._max_attempts = max_attempts
        self._backoff = backoff_min

    # --- enqueue ---

    def enqueue(self, ntype: str, recipient: str, site_id: str,
                reference: str = "", priority: int = 0,
                level: int = 0, date: str = "",
                max_attempts: int | None = None, **fields) -> Notification:
        """Idempotent enqueue; returns the existing row on duplicate key."""
        key = dedup_key(site_id, date, recipient, ntype, reference, level)
        existing = self._repo.get_by_dedup(key)
        if existing is not None:
            return existing
        text = templates.build(ntype, site=site_id, date=date, **fields)
        return self._repo.add(Notification(
            dedup_key=key, recipient=str(recipient), site_id=site_id,
            ntype=ntype, reference=reference, text=text, priority=priority,
            max_attempts=max_attempts or self._max_attempts))

    # --- dispatch ---

    async def dispatch(self, send_fn: Callable[[str, str], object],
                       auth=None, now_iso: str | None = None,
                       limit: int = 50) -> dict:
        """Send all due rows once. Returns {sent, failed, skipped, errors}.

        Eligibility re-check per row: unknown auth -> send (legacy/tests);
        known auth -> active membership at row.site required, and
        pending/rejected roles never receive. Double dispatch is safe:
        claim() lets exactly one caller own each row.
        """
        import inspect

        now = now_iso or _now_iso()
        outcome = {"sent": 0, "failed": 0, "skipped": 0, "errors": []}
        for nid in self._repo.due_ids(now, limit):
            row = self._repo.claim(nid, now)
            if row is None:
                continue  # claimed elsewhere / no longer due
            if not self._eligible(row, auth):
                self._repo.mark_failed(nid, True, "ineligible", None, now)
                outcome["skipped"] += 1
                logger.info("notify skip id=%s to=%s site=%s type=%s: ineligible",
                            nid, row.recipient, row.site_id, row.ntype)
                continue
            try:
                result = send_fn(row.recipient, row.text)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                category, terminal = classify_error(e)
                if terminal or row.attempts + 1 >= row.max_attempts:
                    self._repo.mark_failed(nid, True, category, None, now)
                    outcome["failed"] += 1
                else:
                    delay = self._backoff[min(row.attempts,
                                              len(self._backoff) - 1)]
                    nxt = (datetime.fromisoformat(now)
                           + timedelta(minutes=delay)).isoformat()
                    self._repo.mark_failed(nid, False, category, nxt, now)
                    outcome["failed"] += 1
                outcome["errors"].append(category)
                logger.warning("notify fail id=%s to=%s site=%s type=%s "
                               "attempt=%d result=%s",
                               nid, row.recipient, row.site_id, row.ntype,
                               row.attempts + 1, category)
                continue
            self._repo.mark_sent(nid, now)
            outcome["sent"] += 1
            logger.info("notify sent id=%s to=%s site=%s type=%s", nid,
                        row.recipient, row.site_id, row.ntype)
        return outcome

    def _eligible(self, row: Notification, auth) -> bool:
        if auth is None:
            return True
        try:
            sites = auth.sites_for_user(row.recipient) or []
        except Exception:
            return False
        if row.site_id not in sites:
            return False
        try:
            return bool(auth.get_role(row.recipient)
                        not in ("pending", "rejected", "unknown"))
        except Exception:
            return False

    # --- ack / cancel ---

    def ack(self, nid: int, now_iso: str | None = None) -> bool:
        return self._repo.ack(nid, now_iso or _now_iso())

    def cancel_for(self, site_id: str, reference: str) -> int:
        """Supersede pending rows when their workflow completed."""
        return self._repo.cancel_for_reference(site_id, reference)

    # --- inspection (admin trial utility) ---

    def pending_count(self, site_id: str | None = None) -> int:
        return self._repo.pending_count(site_id)

    def pending_for_site(self, site_id: str, limit: int = 200) -> list:
        return self._repo.pending_for_site(site_id, limit)
