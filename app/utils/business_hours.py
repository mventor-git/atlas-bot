"""
Business hours utility — restricts editing to 8:00 AM - 5:00 PM.

After 5 PM, the bot becomes read-only: users can view drafts and preview PDFs
but cannot create, edit, finalize, lock, or modify any report data.
"""

from datetime import datetime, time

from telegram import Update
from telegram.ext import ContextTypes

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9 (should not happen with 3.12+)
    ZoneInfo = None  # type: ignore

# Business hours
WORK_START = time(8, 0)   # 8:00 AM
WORK_END = time(17, 0)    # 5:00 PM

# Default timezone (overridden via config)
_default_timezone: str | None = None


def set_timezone(timezone_name: str) -> None:
    """Set the default timezone for business hours checking.

    Args:
        timezone_name: IANA timezone name (e.g., 'Asia/Riyadh', 'America/New_York').
    """
    global _default_timezone
    _default_timezone = timezone_name


def get_now_in_timezone(tz_name: str | None = None) -> datetime:
    """Get the current time in the specified timezone.

    Args:
        tz_name: IANA timezone name. If None, uses the configured default
                 or system local time.

    Returns:
        Current datetime in the specified timezone.
    """
    if tz_name is None:
        tz_name = _default_timezone

    if tz_name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
            return datetime.now(tz)
        except (ValueError, TypeError):
            pass

    return datetime.now()

# Callback data patterns that are READ-ONLY (always allowed)
READ_ONLY_CALLBACKS = {
    "dashboard",
    "view_report",
    "open_draft",
    "preview_pdf",
    "search",
    "help",
    "admin_panel",
    "admin_events",
    "admin_health",
    "admin_autolock",
    "new_search",
    "cancel_report",
    "cancel:finalize",
    "cancel:lock",
}


def is_business_hours(now: datetime | None = None, timezone_name: str | None = None) -> bool:
    """Check if the current time is within business hours (8 AM - 5 PM).

    Args:
        now: The datetime to check. Defaults to current time in configured timezone.
        timezone_name: Override timezone. Uses configured default if None.

    Returns:
        True if within business hours, False otherwise.
    """
    if now is None:
        now = get_now_in_timezone(timezone_name)
    current_time = now.time()
    return WORK_START <= current_time < WORK_END


def after_hours_message() -> str:
    """Return a message to show when the bot is outside business hours."""
    return (
        "\U0001f6ab *After Hours* \u2014 The bot is currently in read-only mode.\n\n"
        "Editing is available from *8:00 AM to 5:00 PM*.\n\n"
        "You can still view drafts and preview PDFs using the buttons below."
    )


def is_read_only_callback(callback_data: str) -> bool:
    """Check if a callback data represents a read-only operation.

    Args:
        callback_data: The callback data string.

    Returns:
        True if the operation is read-only and allowed after hours.
    """
    # Exact match
    if callback_data in READ_ONLY_CALLBACKS:
        return True
    # Prefix matches (e.g., view_report:2026-07-12, select_contractor:..., etc.)
    for prefix in ("view_report:", "search_page:"):
        if callback_data.startswith(prefix):
            return True
    return False


async def check_business_hours(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str | None = None,
) -> bool:
    """Check if the operation is allowed during business hours.

    If outside business hours and the operation is not read-only,
    sends an after-hours message and returns False.

    Args:
        update: The Telegram update.
        context: The handler context.
        callback_data: Optional callback data to check. If None,
                      assumes it's an edit operation.

    Returns:
        True if allowed, False if blocked by after-hours policy.
    """
    if is_business_hours():
        return True

    # Check if it's a read-only operation
    if callback_data and is_read_only_callback(callback_data):
        return True

    # Block the operation
    msg = after_hours_message()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")
    return False
