"""Shared authorization gates for bot handlers (ticket-012, P1-2).

Single choke point for read-access denial: pending, rejected, and
unknown users see nothing but the denial string. Works for both
message updates and callback queries (via effective_message).
"""

from telegram import Update
from telegram.ext import ContextTypes

from app.utils.logger import get_logger

logger = get_logger(__name__)

DENIED = "Your permissions don't include access to this information."
PENDING_MSG = (
    "Your account is awaiting approval. An admin will review it soon."
)


def role_of(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Resolve the caller's role; 'unknown' when unresolvable (deny-safe)."""
    try:
        auth = (context.bot_data or {}).get("authorization_service")
        if auth is None:
            return "unknown"
        return auth.get_role(str(update.effective_user.id))
    except Exception as e:
        logger.warning("Role resolution failed: %s", e)
        return "unknown"


async def _send_denial(update: Update, text: str) -> None:
    """Deliver denial via the first working channel (message/callback safe)."""
    query = getattr(update, "callback_query", None)
    message = getattr(update, "message", None)
    effective = getattr(update, "effective_message", None)
    attempts = []
    if effective is not None:
        attempts.append(lambda: effective.reply_text(text))
    if message is not None:
        attempts.append(lambda: message.reply_text(text))
    if query is not None:
        attempts.append(lambda: query.edit_message_text(text))
    for send in attempts:
        try:
            await send()
            return
        except Exception:
            continue
    logger.warning("Denial send failed on all channels.")


async def require_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Gate read surfaces (reports, PDFs, search, comparison).

    Returns:
        True if the caller may proceed; False after sending denial.
    """
    role = role_of(update, context)
    if role in ("pending", "rejected", "unknown"):
        message = PENDING_MSG if role == "pending" else DENIED
        await _send_denial(update, message)
        return False
    return True
