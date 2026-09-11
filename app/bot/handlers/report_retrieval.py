"""
Report Retrieval Handler — Lets users request PDFs by typing a date.

Commands:
  - /get <date>   — retrieve PDF for a specific date
  - /get yesterday — retrieve yesterday's PDF

Text messages that look like dates (e.g. "12-07-2026", "yesterday", "2026/07/12")
are also intercepted and treated as PDF requests when no active conversation
state exists.

Supported date formats:
  - YYYY-MM-DD, YYYY/MM/DD
  - DD-MM-YYYY, DD/MM/YYYY
  - DD.MM.YYYY
  - YYYYMMDD (compact)
  - "yesterday", "yest" (English)
  - "امس", "أمس" (Arabic)
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from app.models.database import Report, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.utils.date_parser import parse_date_string, is_likely_date

logger = logging.getLogger(__name__)


# ─── Messages ───────────────────────────────────────────────────


NO_REPORT_FOUND = (
    "\U0001f50d *No Report Found*\n\n"
    "No labor report exists for `{date}`.\n\n"
    "Use `/new {date}` to create one."
)

NO_PDF_AVAILABLE = (
    "\U0001f4c4 *No PDF Available*\n\n"
    "A report exists for `{date}` but no PDF file was generated.\n"
    "Try finalizing it first with `/finalize {date}`."
)

PDF_FOUND = (
    "\U0001f4c4 *Report PDF — {date}*\n\n"
    "*Status:* {status}\n"
    "*Contractors:* {contractor_count}\n"
    "*Total Workers:* {total_workers}"
)

FILE_NOT_FOUND = (
    "\U000026a0 *File Missing*\n\n"
    "The PDF file for `{date}` was recorded but could not be found on disk.\n"
    "Path: `{path}`\n\n"
    "You may need to regenerate it."
)


# ─── Helpers ───────────────────────────────────────────────────


def _get_report(context: ContextTypes.DEFAULT_TYPE, date_str: str) -> Optional[Report]:
    """Get a report by date string from bot_data.

    Args:
        context: The handler context (contains report_repository).
        date_str: Date string in YYYY-MM-DD format.

    Returns:
        Report object or None.
    """
    repo: ReportRepository = context.bot_data.get("report_repository")
    if repo is None:
        logger.error("report_repository not found in bot_data")
        return None
    try:
        return repo.get_by_date(date_str)
    except Exception as e:
        logger.error("Failed to get report for %s: %s", date_str, e)
        return None


def _format_status(status: ReportStatus) -> str:
    """Format a ReportStatus for display."""
    return status.value.replace("_", " ").title()


def _count_workers(report: Report) -> int:
    """Sum workers across all items in a report."""
    return sum(
        item.workers or 0
        for item in (report.items or [])
    )


async def _send_pdf(
    update: Update,
    report: Report,
    date_str: str,
) -> None:
    """Send a PDF file to the user, or explain why it's not available.

    Args:
        update: The Telegram update.
        report: The report object.
        date_str: The formatted date string for messages.
    """
    # Determine which PDF to send (prefer preview if available)
    pdf_path_str = report.preview_pdf_path or report.pdf_path

    if not pdf_path_str:
        await update.message.reply_text(
            NO_PDF_AVAILABLE.format(date=date_str),
            parse_mode="Markdown",
        )
        return

    pdf_path = Path(pdf_path_str)

    if not pdf_path.exists():
        await update.message.reply_text(
            FILE_NOT_FOUND.format(date=date_str, path=pdf_path_str),
            parse_mode="Markdown",
        )
        return

    # Count contractors and workers
    contractor_count = len(report.items) if report.items else 0
    total_workers = _count_workers(report)
    status = _format_status(report.status)

    # Send the PDF
    try:
        with open(pdf_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"labor_report_{date_str}.pdf",
                caption=PDF_FOUND.format(
                    date=date_str,
                    status=status,
                    contractor_count=contractor_count,
                    total_workers=total_workers,
                ),
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error("Failed to send PDF for %s: %s", date_str, e)
        await update.message.reply_text(
            f"Failed to send the PDF. Error: {e}",
        )


async def _handle_date_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parsed_date: date,
) -> None:
    """Common logic: look up a report for the given date and send its PDF.

    Args:
        update: The Telegram update.
        context: The handler context.
        parsed_date: The parsed date.
    """
    date_str = parsed_date.isoformat()
    display_date = parsed_date.strftime("%Y-%m-%d")

    report = _get_report(context, date_str)

    if report is None:
        await update.message.reply_text(
            NO_REPORT_FOUND.format(date=display_date),
            parse_mode="Markdown",
        )
        return

    await _send_pdf(update, report, display_date)


# ─── Command handler: /get <date> ──────────────────────────────


async def get_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /get command — retrieve a PDF for a specific date.

    Usage:
        /get 2026-07-12
        /get yesterday
        /get 12/07/2026
    """
    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    args = context.args

    if not args:
        await update.message.reply_text(
            "\U0001f4c5 *Usage:* `/get <date>`\n\n"
            "Examples:\n"
            "  `/get 2026-07-12`\n"
            "  `/get yesterday`\n"
            "  `/get 12/07/2026`\n"
            "  `/get امس`\n\n"
            "You can also just type a date directly!",
            parse_mode="Markdown",
        )
        return

    date_text = " ".join(args)
    parsed = parse_date_string(date_text)

    if parsed is None:
        await update.message.reply_text(
            f"I couldn't understand that date: `{date_text}`\n\n"
            "Try formats like: `2026-07-12`, `12-07-2026`, or `yesterday`.",
            parse_mode="Markdown",
        )
        return

    await _handle_date_request(update, context, parsed)


# ─── Text handler: auto-detect dates ───────────────────────────


async def handle_date_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the message looks like a date and handle it.

    Designed to be called from the main text handler when state is None.
    Returns True if the message was a date and was handled.

    Args:
        update: The Telegram update.
        context: The handler context.

    Returns:
        True if the message was handled as a date.
    """
    text = (update.message.text or "").strip()

    if not is_likely_date(text):
        return False

    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return True

    parsed = parse_date_string(text)
    if parsed is None:
        return False

    await _handle_date_request(update, context, parsed)
    return True


# ─── Registration ──────────────────────────────────────────────


def get_registration_handlers() -> list:
    """Return handlers to register in the bot."""
    return [
        CommandHandler("get", get_report_command),
    ]
