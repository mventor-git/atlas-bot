"""Shared notify() helper (031): enqueue + best-effort inline dispatch.

Handlers never send workflow notices directly anymore: they enqueue a
durable row (deduped) and attempt immediate delivery so UX is unchanged
on success. Failures persist in the outbox and retry via the scheduler.

Falls back to a direct send only when no outbox is wired (legacy tests).
"""

from __future__ import annotations

from telegram.ext import ContextTypes

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _outbox(context: ContextTypes.DEFAULT_TYPE):
    return (context.bot_data or {}).get("notification_outbox")


async def notify(context: ContextTypes.DEFAULT_TYPE, ntype: str,
                 recipient: str | int, site_id: str, reference: str = "",
                 priority: int = 0, level: int = 0, date: str = "",
                 max_attempts: int | None = None, **fields):
    """Enqueue a workflow notice and try to deliver it right away."""
    outbox = _outbox(context)
    if outbox is None:
        try:
            await context.bot.send_message(
                chat_id=int(recipient),
                text=_render(ntype, site_id, date, fields),
                parse_mode="Markdown")
        except Exception as e:
            logger.warning("Direct notify failed for %s: %s", recipient, e)
        return None
    row = outbox.enqueue(ntype, recipient, site_id, reference=reference,
                         priority=priority, level=level, date=date,
                         max_attempts=max_attempts, **fields)
    auth = (context.bot_data or {}).get("authorization_service")

    async def _send(to: str, text: str) -> None:
        await context.bot.send_message(chat_id=int(to), text=text,
                                       parse_mode="Markdown")

    await outbox.dispatch(_send, auth=auth)
    return row


def _render(ntype: str, site_id: str, date: str, fields: dict) -> str:
    from app.services import notification_templates as templates

    try:
        return templates.build(ntype, site=site_id, date=date, **fields)
    except Exception:
        return f"Atlas notification: {ntype} ({site_id})."
