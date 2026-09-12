"""Comparison handlers for Labor-Report Telegram bot. (mventor-ticket-019)

Provides /compare command to compare two daily reports side by side.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from app.services.daily_comparison_service import (
    DailyComparisonService,
    DailyComparisonError,
)
from app.utils.date_parser import parse_date_string
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a comparison flow by asking for the first date.

    Usage: /compare
    The user will be prompted for two dates via two steps.
    """
    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    from app.bot import site_session
    from app.services import report_visibility as visibility

    chat_id = str(update.effective_user.id)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="compare",
                                    hint="Press /compare again.")
        return
    auth = context.bot_data.get("authorization_service")
    if visibility.resolve(auth, chat_id, site) == visibility.OWNER:
        await update.message.reply_text(
            "Comparison needs reviewer access.")
        return
    context.user_data["state"] = "awaiting_compare_date_a"
    context.user_data["compare_site"] = site
    await update.message.reply_text(
        "\U0001f4ca *Daily Report Comparison*\n\n"
        "Send the *first date* (YYYY-MM-DD) to compare.\n\n"
        "Example: `2026-07-10`",
        parse_mode="Markdown",
    )


async def handle_compare_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages during the comparison flow.

    Routes to the appropriate step based on current user state:
    - 'awaiting_compare_date_a': first date
    - 'awaiting_compare_date_b': second date (perform comparison)
    """
    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        context.user_data.pop("state", None)
        return
    state = context.user_data.get("state")

    if state == "awaiting_compare_date_a":
        await _handle_date_a(update, context)
    elif state == "awaiting_compare_date_b":
        await _handle_date_b(update, context)


async def _handle_date_a(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the first date in a comparison flow."""
    text = update.message.text.strip()
    parsed = parse_date_string(text)
    if parsed is None:
        await update.message.reply_text(
            "Invalid date format. Please use YYYY-MM-DD (e.g., 2026-07-10)."
        )
        return

    date_iso = parsed.isoformat()
    context.user_data["compare_date_a"] = date_iso
    context.user_data["state"] = "awaiting_compare_date_b"

    await update.message.reply_text(
        f"First date: *{date_iso}*\n\n"
        "Now send the *second date* (YYYY-MM-DD) to compare against.",
        parse_mode="Markdown",
    )


async def _handle_date_b(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the second date and perform the comparison."""
    text = update.message.text.strip()
    parsed = parse_date_string(text)
    if parsed is None:
        await update.message.reply_text(
            "Invalid date format. Please use YYYY-MM-DD (e.g., 2026-07-11)."
        )
        return

    date_b_iso = parsed.isoformat()
    date_a_iso = context.user_data.get("compare_date_a", "")

    # Clear session state
    context.user_data["state"] = "idle"
    context.user_data.pop("compare_date_a", None)
    site = context.user_data.pop("compare_site", None)

    if date_a_iso == date_b_iso:
        await update.message.reply_text(
            "You entered the same date for both reports. "
            "Please use two different dates to compare."
        )
        return

    # Perform the comparison
    comparison_service: DailyComparisonService | None = context.bot_data.get(
        "daily_comparison_service"
    )
    if comparison_service is None:
        await update.message.reply_text(
            "Comparison service is not available. Please try again later."
        )
        return

    try:
        result = comparison_service.compare(date_a_iso, date_b_iso,
                                            site_id=site)
        await update.message.reply_text(
            result.format_summary(),
            parse_mode="Markdown",
        )
    except DailyComparisonError as e:
        await update.message.reply_text(f"\u26a0\ufe0f {e.message}")
    except Exception as e:
        logger.error("Comparison failed for %s vs %s: %s", date_a_iso, date_b_iso, e)
        await update.message.reply_text(
            "An error occurred while comparing reports. Please try again."
        )


def get_registration_handlers() -> list:
    """Return the list of handlers for comparison features."""
    return [
        CommandHandler("compare", compare_command),
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_compare_text,
        ),
    ]
