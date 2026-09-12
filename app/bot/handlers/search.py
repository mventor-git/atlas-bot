"""Search handlers for Labor-Report Telegram bot."""

from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from app.repositories.report_repository import ReportRepository
from app.models.search import SearchQuery, SearchHit
from app.services.universal_search_service import UniversalSearchService
from app.bot.keyboards import search_results_keyboard
from app.utils.date_parser import parse_date_string
from app.utils.logger import get_logger

PDF_FOUND = (
    "\U0001f4c4 *Report PDF — {date}*\n\n"
    "*Status:* {status}\n"
    "*Contractors:* {contractor_count}\n"
    "*Total Workers:* {total_workers}"
)

NO_PDF_AVAILABLE = (
    "\U0001f4c4 *No PDF Available*\n\n"
    "A report exists for `{date}` but no PDF file was generated.\n"
)

FILE_NOT_FOUND = (
    "\U000026a0 *File Missing*\n\n"
    "The PDF file for `{date}` was recorded but could not be found on disk.\n"
    "Path: `{path}`"
)

logger = get_logger(__name__)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start search flow."""
    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    context.user_data["state"] = "awaiting_search_query"
    await update.message.reply_text(
        "🔍 *Search Reports*\n\n"
        "Send a date (YYYY-MM-DD), contractor name, or keyword to search.",
        parse_mode="Markdown",
    )


async def _send_report_pdf(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    report,
    date_str: str,
) -> bool:
    """Send a report's PDF file if available.

    Args:
        update: The Telegram update.
        context: The handler context.
        report: The report object (must have pdf_path or preview_pdf_path).
        date_str: The ISO date string for messages.

    Returns:
        True if the PDF was sent, False otherwise.
    """
    pdf_path_str = report.preview_pdf_path or report.pdf_path

    if not pdf_path_str:
        return False

    pdf_path = Path(pdf_path_str)

    if not pdf_path.exists():
        return False

    # Count contractors and workers
    contractor_count = len(report.items) if report.items else 0
    total_workers = sum(
        (item.workers or 0) for item in (report.items or [])
    )
    status = (report.status.value if report.status else "N/A").replace("_", " ").title()

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
        return True
    except Exception as e:
        logger.error("Failed to send PDF for %s: %s", date_str, e)
        return False


async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process search query text."""
    if context.user_data.get("state") != "awaiting_search_query":
        return

    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        context.user_data.pop("state", None)
        return
    query_text = update.message.text.strip()
    search_service: UniversalSearchService | None = context.bot_data.get("universal_search_service")
    repo: ReportRepository = context.bot_data["report_repository"]

    # ── Try direct date lookup first (any format) ─────────────
    parsed_date = parse_date_string(query_text)
    if parsed_date is not None:
        from app.bot import site_session

        site = site_session.resolve_site(update, context)
        if site is None:
            await site_session.ask_site(update, context, resume="search",
                                        hint="Send your search again.")
            return
        date_iso = parsed_date.isoformat()
        report = repo.get_by_date(date_iso, site_id=site)
        if report is not None:
            from app.services import report_visibility as visibility

            chat_id = str(update.effective_user.id)
            auth = context.bot_data.get("authorization_service")
            if visibility.resolve(auth, chat_id, report.site_id) \
                    == visibility.OWNER:
                await _show_report_detail(update, context, report)
                return
            # Send the PDF if available, otherwise show report detail
            if await _send_report_pdf(update, context, report, date_iso):
                return
            # Fallback: show report detail text
            await _show_report_detail(update, context, report)
            return

    try:
        if search_service is None:
            # Legacy fallback without search service
            from app.bot import site_session as _ss2

            _site2 = _ss2.resolve_site(update, context)
            report = repo.get_by_date(query_text, site_id=_site2) \
                if _looks_like_date(query_text) and _site2 else None
            if report is not None:
                await _show_report_detail(update, context, report)
                return
            await update.message.reply_text(
                f"No results found for '{query_text}'.\n\n"
                "Universal Search is not configured.",
            )
            return

        from app.bot import site_session as _ss

        _site = _ss.resolve_site(update, context)
        if _site is None:
            await _ss.ask_site(update, context, resume="search",
                               hint="Send your search again.")
            return
        result = search_service.search(SearchQuery(text=query_text, page=0, page_size=5,
                                                   site_id=_site))
        if result.has_results:
            context.user_data["search_site"] = _site
        if result.has_results:
            await _show_search_results(update, context, result.hits, query_text, result.total_count, result.total_pages)
        else:
            await update.message.reply_text(
                f"{result.no_results_message}\n\n"
                "Try a date (YYYY-MM-DD), contractor name, code, type, zone, status, or worker count.",
            )
    except Exception as e:
        logger.error("Search failed for '%s': %s", query_text, e)
        await update.message.reply_text("Search failed. Please try again.")


async def handle_view_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View a specific report by date from search results."""
    query = update.callback_query
    await query.answer()

    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    date_str = query.data.replace("view_report:", "")
    repo: ReportRepository = context.bot_data["report_repository"]

    try:
        from app.bot import site_session

        site = site_session.resolve_site(update, context)
        if site is None:
            await site_session.ask_site(update, context, resume=f"view:{date_str}",
                                        hint="Press the button again.")
            return
        report = repo.get_by_date(date_str, site_id=site)
        if report is None:
            await query.edit_message_text(f"No report found for {date_str}.")
            return

        await _show_report_detail(update, context, report)
    except Exception as e:
        logger.error("Failed to view report %s: %s", date_str, e)
        await query.edit_message_text("Failed to load report.")


async def handle_search_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle search result pagination."""
    query = update.callback_query
    await query.answer()

    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    page = int(query.data.replace("search_page:", ""))
    query_text = context.user_data.get("last_search_query_text", "")
    total_pages = context.user_data.get("last_search_total_pages", 1)
    page_size = context.user_data.get("last_search_page_size", 5)
    search_service: UniversalSearchService | None = context.bot_data.get("universal_search_service")

    if search_service is None or not query_text:
        await query.edit_message_text("Search session expired. Please start a new search.")
        return

    result = search_service.search(SearchQuery(text=query_text, page=page, page_size=page_size,
                                                   site_id=context.user_data.get("search_site")))
    page_results = _format_search_hits(result.hits)
    total_pages = result.total_pages

    keyboard = search_results_keyboard(page_results, page=page, total_pages=total_pages)
    context.user_data["last_search_page"] = page
    context.user_data["last_search_total_pages"] = total_pages

    await query.edit_message_text(
        f"*Search Results for '{query_text}'*\n\n{result.total_count} report(s) found.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def handle_new_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new search."""
    query = update.callback_query
    await query.answer()

    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    context.user_data["state"] = "awaiting_search_query"
    await query.edit_message_text(
        "🔍 Send a date (YYYY-MM-DD), contractor name, or keyword to search.",
        parse_mode="Markdown",
    )


async def _show_report_detail(update, context, report) -> None:
    """Show detailed view of a report.

    Works both from callback queries (edits the current message)
    and from direct text messages (sends a new reply).
    Audience rule (029): OWNER gets the simple text and never the file.
    If the report has a PDF file, it is sent as a document.
    """
    from app.services import report_visibility as visibility

    chat_id = str(update.effective_user.id)
    auth = context.bot_data.get("authorization_service")
    audience = visibility.resolve(auth, chat_id, report.site_id)
    if audience == visibility.NONE:
        sender = update.callback_query.edit_message_text \
            if update.callback_query else update.message.reply_text
        await sender("You don't have access to this site's reports.")
        return
    if audience == visibility.OWNER:
        if not visibility.owner_may_see_status(
                report.status.value if report.status else None):
            sender = update.callback_query.edit_message_text \
                if update.callback_query else update.message.reply_text
            await sender("This report is not yet available.")
            return
        text = visibility.render_simple(report) + \
            "\n\n_PDF download needs reviewer access._"
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return

    total_workers = sum((i.workers or 0) for i in (report.items or []))
    contractor_count = len(report.items) if report.items else 0

    text = (
        f"📋 *Report: {report.date}*\n\n"
        f"*Status:* {report.status.value if report.status else 'N/A'}\n"
        f"*Contractors:* {contractor_count}\n"
        f"*Total Workers:* {total_workers}\n"
        f"*Day:* {report.day}\n"
    )
    if report.finalized_at:
        text += f"*Finalized:* {report.finalized_at[:10]}\n"

    if report.items:
        text += "\n*Contractors:*\n"
        for i, item in enumerate(report.items, 1):
            w = item.workers or 0
            text += f"{i}. {item.contractor} — {w} workers\n"

    # Use appropriate method based on context
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        text += "\n📄 *PDF:* Use `/get` to download the PDF file."
        await update.message.reply_text(text, parse_mode="Markdown")

    # Try to send the PDF file if it exists on disk
    pdf_path_str = report.preview_pdf_path or report.pdf_path
    if pdf_path_str:
        pdf_path = Path(pdf_path_str)
        if pdf_path.exists():
            try:
                with open(pdf_path, "rb") as f:
                    if update.callback_query:
                        await update.callback_query.message.reply_document(
                            document=f,
                            filename=f"labor_report_{report.date}.pdf",
                            caption=PDF_FOUND.format(
                                date=report.date,
                                status=(report.status.value if report.status else "N/A").replace("_", " ").title(),
                                contractor_count=contractor_count,
                                total_workers=total_workers,
                            ),
                            parse_mode="Markdown",
                        )
                    else:
                        await update.message.reply_document(
                            document=f,
                            filename=f"labor_report_{report.date}.pdf",
                            caption=PDF_FOUND.format(
                                date=report.date,
                                status=(report.status.value if report.status else "N/A").replace("_", " ").title(),
                                contractor_count=contractor_count,
                                total_workers=total_workers,
                            ),
                            parse_mode="Markdown",
                        )
            except Exception as e:
                logger.error("Failed to send PDF for %s: %s", report.date, e)


async def _show_search_results(update, context, results, query_text, total_count=None, total_pages=None) -> None:
    """Display search results with pagination."""
    formatted = _format_search_hits(results)

    page_size = 5
    total_count = total_count if total_count is not None else len(formatted)
    total_pages = total_pages if total_pages is not None else max(1, (len(formatted) + page_size - 1) // page_size)
    page_results = formatted[:page_size]

    context.user_data["last_search_results"] = formatted
    context.user_data["last_search_query_text"] = query_text
    context.user_data["last_search_page"] = 0
    context.user_data["last_search_total_pages"] = total_pages
    context.user_data["last_search_page_size"] = page_size

    keyboard = search_results_keyboard(page_results, page=0, total_pages=total_pages)
    await update.message.reply_text(
        f"*Search Results for '{query_text}'*\n\n{total_count} report(s) found.",
        reply_markup=keyboard, parse_mode="Markdown",
    )


def _format_search_hits(results) -> list[dict]:
    """Convert search hits or legacy reports to keyboard dictionaries."""
    formatted = []
    for hit in results:
        if isinstance(hit, SearchHit):
            formatted.append({
                "date": hit.date,
                "contractor_count": hit.contractor_count,
                "status": hit.status.value,
            })
        else:
            c = len(hit.items) if hasattr(hit, "items") and hit.items else 0
            formatted.append({"date": hit.date, "contractor_count": c})
    return formatted


def _looks_like_date(text: str) -> bool:
    """Check if text looks like a date in YYYY-MM-DD or DD-MM-YYYY format."""
    parts = text.split("-")
    if len(parts) != 3:
        return False
    if not all(p.isdigit() for p in parts):
        return False
    # YYYY-MM-DD: first part is 4 digits
    if len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2:
        return True
    # DD-MM-YYYY: third part is 4 digits
    if len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 4:
        return True
    return False


def get_registration_handlers() -> list:
    return [
        CommandHandler("search", search_command),
        CallbackQueryHandler(handle_view_report_callback, pattern="^view_report:"),
        CallbackQueryHandler(handle_search_page, pattern="^search_page:"),
        CallbackQueryHandler(handle_new_search, pattern="^new_search$"),
    ]
