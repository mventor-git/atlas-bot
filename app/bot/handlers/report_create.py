from datetime import date, datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.auto_save_service import AutoSaveService
from app.services.smart_suggestion_service import SmartSuggestionService
from app.services.contractor_search import ContractorSearchService
from app.services.one_click_yesterday_service import OneClickYesterdayService
from app.services.arabic_date_service import ArabicDateService
from app.services.authorization_service import AuthorizationService
from app.services.audit_service import AuditService
from app.bot.keyboards import contractor_selection_keyboard, zone_selection_keyboard, report_actions_keyboard, confirmation_keyboard
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def new_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Business hours check
    from app.utils.business_hours import check_business_hours
    if not await check_business_hours(update, context):
        return

    telegram_user = str(update.effective_user.id)
    today = date.today().isoformat()
    day_name = ArabicDateService.get_day_name(date.today())
    suggestion_service: SmartSuggestionService = context.bot_data["suggestion_service"]
    auto_save_service: AutoSaveService = context.bot_data["auto_save_service"]

    try:
        report = Report(
            date=today,
            day=day_name,
            telegram_user=telegram_user,
            status=ReportStatus.DRAFT,
        )
        report = auto_save_service.save_draft(report, telegram_user)
        context.user_data["current_report"] = report
        context.user_data["state"] = "awaiting_contractor_name"

        text = (
            f"\U0001f4dd *New Report Created*\n\n"
            f"Date: {today}\n\n"
            f"Send me the contractor name, or type /done to finish."
        )

        suggestions = suggestion_service.get_suggestions(telegram_user, today, max_results=10)
        if suggestions:
            contractor_tuples = [
                (f"{c.name} ({c.type})" if c.type else c.name, str(i))
                for i, c in enumerate(suggestions)
            ]
            context.user_data["search_results"] = contractor_tuples
            context.user_data["search_contractor_objects"] = list(suggestions)
            context.user_data["search_page"] = 0
            context.user_data["search_total_pages"] = 1
            keyboard = contractor_selection_keyboard(contractor_tuples)
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")

        logger.info("New report created: date=%s, user=%s", today, telegram_user)
    except Exception as e:
        logger.error("Failed to create new report for user %s: %s", telegram_user, e)
        await update.message.reply_text("An error occurred while creating the report. Please try again.")


async def copy_yesterday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Business hours check
    from app.utils.business_hours import check_business_hours
    if not await check_business_hours(update, context):
        return

    telegram_user = str(update.effective_user.id)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    day_name = ArabicDateService.get_day_name(date.today())
    one_click_service: OneClickYesterdayService = context.bot_data["one_click_yesterday_service"]
    auto_save_service: AutoSaveService = context.bot_data["auto_save_service"]

    try:
        draft = one_click_service.copy_yesterday(
            yesterday_date=yesterday,
            today_date=today,
            today_day=day_name,
            telegram_user=telegram_user,
        )

        if draft is None:
            await update.message.reply_text("No report from yesterday to copy.")
            logger.info("No yesterday report to copy for user %s", telegram_user)
            return

        draft = auto_save_service.save_draft(draft, telegram_user)
        context.user_data["current_report"] = draft
        context.user_data["state"] = "idle"

        items_text = ""
        total_workers = 0
        for item in draft.items:
            w = item.workers or 0
            total_workers += w
            items_text += f"\n\u2022 {item.contractor}: {w} workers"

        text = (
            f"\U0001f4cb *Report Copied from Yesterday*\n\n"
            f"Date: {today}\n"
            f"Source: {yesterday}\n"
            f"Contractors: {len(draft.items)}\n"
            f"Total Workers: {total_workers}\n"
            f"{items_text}"
        )

        keyboard = report_actions_keyboard("draft")
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        logger.info("Yesterday report copied: user=%s, today=%s, yesterday=%s", telegram_user, today, yesterday)

        # Generate and send PDF
        try:
            preview_service = context.bot_data.get("pdf_preview_service")
            if preview_service and draft.items:
                pdf_path = preview_service.generate_preview(draft)
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=InputFile(Path(pdf_path).read_bytes(), filename=f"Labor_Report_{today}.pdf"),
                )
        except Exception as pdf_e:
            logger.warning("Could not send PDF after copy yesterday: %s", pdf_e)
    except Exception as e:
        logger.error("Failed to copy yesterday report for user %s: %s", telegram_user, e)
        await update.message.reply_text("An error occurred while copying yesterday's report.")


async def handle_contractor_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") != "awaiting_contractor_name":
        return

    telegram_user = str(update.effective_user.id)
    contractor_name = update.message.text.strip()
    contractor_search: ContractorSearchService = context.bot_data["contractor_search"]

    try:
        results = contractor_search.search(contractor_name, max_results=20)

        if not results:
            await update.message.reply_text(
                "No contractor found with that name. Try again or type /done."
            )
            logger.info("No contractor found for query '%s' (user=%s)", contractor_name, telegram_user)
            return

        # Use numeric index as callback code to avoid Telegram's 64-byte limit
        contractor_tuples = [
            (f"{c.name} ({c.type})" if c.type else c.name, str(i))
            for i, c in enumerate(results)
        ]
        context.user_data["search_contractor_objects"] = list(results)
        total_pages = max(1, (len(contractor_tuples) + 9) // 10)
        context.user_data["search_results"] = contractor_tuples
        context.user_data["search_page"] = 0
        context.user_data["search_total_pages"] = total_pages
        context.user_data["state"] = "awaiting_contractor_selection"

        keyboard = contractor_selection_keyboard(contractor_tuples, page=0, total_pages=total_pages, page_size=10)
        await update.message.reply_text(
            "Select a contractor:", reply_markup=keyboard, parse_mode="Markdown"
        )
        logger.info("Contractor search results shown for '%s' (user=%s)", contractor_name, telegram_user)
    except Exception as e:
        logger.error("Error searching contractor '%s' for user %s: %s", contractor_name, telegram_user, e)
        await update.message.reply_text("An error occurred while searching for contractors.")


async def handle_contractor_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_user = str(update.effective_user.id)

    try:
        idx_str = query.data.replace("select_contractor:", "")
        idx = int(idx_str)
        search_results: list[tuple[str, str]] = context.user_data.get("search_results", [])
        contractor_objects: list = context.user_data.get("search_contractor_objects", [])

        if idx < 0 or idx >= len(search_results):
            raise ValueError(f"Invalid contractor index: {idx}")

        display_name, _ = search_results[idx]

        # Extract raw name and type from contractor objects
        if idx < len(contractor_objects):
            raw_name = contractor_objects[idx].name
            contractor_type = contractor_objects[idx].type or None
        else:
            # Fallback: parse display name (should not happen in normal flow)
            raw_name = display_name
            contractor_type = None

        context.user_data["selected_contractor"] = display_name
        """Display name shown in messages (e.g., 'Name (Type)')."""
        context.user_data["selected_contractor_raw_name"] = raw_name
        """Raw contractor name for the Excel contractor column (C)."""
        context.user_data["selected_contractor_type"] = contractor_type
        """Contractor type for the Excel type column (D)."""
        context.user_data["selected_contractor_code"] = raw_name
        context.user_data["state"] = "awaiting_worker_count"

        await query.edit_message_text(
            f"How many workers for {display_name}? (enter a number)",
            parse_mode="Markdown",
        )
        logger.info("Contractor selected: %s (user=%s)", display_name, telegram_user)
    except Exception as e:
        logger.error("Error in contractor selection for user %s: %s", telegram_user, e)
        await query.edit_message_text("An error occurred. Please try again.")


async def handle_worker_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") != "awaiting_worker_count":
        return

    telegram_user = str(update.effective_user.id)

    try:
        text = update.message.text.strip()
        if not text.isdigit():
            await update.message.reply_text("Please enter a valid number.")
            return

        workers = int(text)
        contractor_name = context.user_data.get("selected_contractor", "Unknown")

        # Store workers in user_data for now (don't save yet)
        context.user_data["current_workers"] = workers
        context.user_data["state"] = "awaiting_zone"

        # Show zone selection keyboard
        contractor_search: ContractorSearchService = context.bot_data.get("contractor_search")
        if contractor_search:
            zones = contractor_search.get_all_zones()
            zone_names = [z.name for z in zones]
            keyboard = zone_selection_keyboard(zone_names)
            await update.message.reply_text(
                f"Workers: {workers}\n\nNow select the work zone for {contractor_name}, or skip:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        else:
            # Fallback: skip zone directly
            context.user_data["current_zone"] = None
            context.user_data["state"] = "awaiting_details"
            await update.message.reply_text(
                f"Workers: {workers}\n\nEnter the work details (column G), or type /skip to leave empty:",
                parse_mode="Markdown",
            )

        logger.info("Worker count entered: contractor=%s, workers=%d, user=%s", contractor_name, workers, telegram_user)
    except Exception as e:
        logger.error("Error adding worker count for user %s: %s", telegram_user, e)
        await update.message.reply_text("An error occurred while adding the contractor. Please try again.")


async def handle_zone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle zone selection from the keyboard."""
    query = update.callback_query
    await query.answer()
    telegram_user = str(update.effective_user.id)

    try:
        zone = query.data.replace("select_zone:", "")
        if zone == "__skip__":
            context.user_data["current_zone"] = None
        else:
            context.user_data["current_zone"] = zone

        context.user_data["state"] = "awaiting_details"
        await query.edit_message_text(
            f"Zone: {zone if zone != '__skip__' else 'Skipped'}\n\n"
            f"Enter the work details to write in column G, or type /skip to leave empty:",
            parse_mode="Markdown",
        )
        logger.info("Zone selected: %s (user=%s)", zone, telegram_user)
    except Exception as e:
        logger.error("Error in zone selection for user %s: %s", telegram_user, e)
        await query.edit_message_text("An error occurred. Please try again.")


async def handle_details_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle details text input (column G) or skip."""
    if context.user_data.get("state") != "awaiting_details":
        return

    telegram_user = str(update.effective_user.id)
    auto_save_service: AutoSaveService = context.bot_data["auto_save_service"]

    try:
        text = update.message.text.strip()
        if text.lower() == "/skip" or text.lower() == "skip":
            details = None
        else:
            details = text

        # Build the ReportItem with all collected data
        contractor_name = context.user_data.get("selected_contractor_raw_name")
        if not contractor_name:
            contractor_name = context.user_data.get("selected_contractor", "Unknown")
        contractor_type = context.user_data.get("selected_contractor_type")
        workers = context.user_data.get("current_workers", 0)
        zone = context.user_data.get("current_zone")

        item = ReportItem(
            contractor=contractor_name,
            type=contractor_type,
            workers=workers,
            zone=zone,
            details=details,
            contractor_code=context.user_data.get("selected_contractor_code"),
        )

        report: Report = context.user_data.get("current_report")
        if report is None:
            await update.message.reply_text("No active report. Please start with /new.")
            return

        report = auto_save_service.auto_save_item(report, item, telegram_user)
        context.user_data["current_report"] = report

        # Audit log the addition
        try:
            audit: AuditService = context.bot_data.get("audit_service")
            if audit:
                auth: AuthorizationService = context.bot_data.get("authorization_service")
                role = auth.get_role(telegram_user) if auth else "unknown"
                audit.log_added(telegram_user, role, report, item)
        except Exception as audit_e:
            logger.warning("Failed to audit log addition: %s", audit_e)

        # Clear temporary data
        for key in ("selected_contractor", "selected_contractor_raw_name", "selected_contractor_type",
                     "selected_contractor_code", "current_workers", "current_zone"):
            context.user_data.pop(key, None)

        # Show contractor list again for adding more
        contractor_search = context.bot_data.get("contractor_search")
        if contractor_search:
            all_contractors = contractor_search.get_all_contractors()
            contractor_tuples = [
                (f"{c.name} ({c.type})" if c.type else c.name, str(i))
                for i, c in enumerate(all_contractors)
            ]
            context.user_data["search_contractor_objects"] = list(all_contractors)
            total_pages = max(1, (len(contractor_tuples) + 9) // 10)
            context.user_data["search_results"] = contractor_tuples
            context.user_data["search_page"] = 0
            context.user_data["search_total_pages"] = total_pages
            context.user_data["state"] = "awaiting_contractor_selection"
            keyboard = contractor_selection_keyboard(contractor_tuples, page=0, total_pages=total_pages)
            await update.message.reply_text(
                f"\u2705 *Added {contractor_name}*\n"
                f"Workers: {workers}\n"
                f"Zone: {zone or '—'}\n"
                f"Details: {details or '—'}\n\n"
                f"Select another contractor from the list, or type /done to finish:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        else:
            context.user_data["state"] = "awaiting_contractor_name"
            await update.message.reply_text(
                f"\u2705 *Added {contractor_name}*\n\n"
                f"Send another contractor name, or type /done to finish.",
                parse_mode="Markdown",
            )

        logger.info(
            "Contractor added: %s, workers=%d, zone=%s, user=%s",
            contractor_name, workers, zone, telegram_user,
        )
    except Exception as e:
        logger.error("Error adding contractor details for user %s: %s", telegram_user, e)
        await update.message.reply_text("An error occurred. Please try again.")


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skip the current step (zone selection or details input)."""
    from app.utils.business_hours import check_business_hours
    if not await check_business_hours(update, context):
        return
    state = context.user_data.get("state")

    if state == "awaiting_details":
        await handle_details_input(update, context)
    elif state == "awaiting_zone":
        context.user_data["current_zone"] = None
        context.user_data["state"] = "awaiting_details"
        await update.message.reply_text(
            "Zone: Skipped\n\n"
            "Enter the work details to write in column G, or type /skip to leave empty:",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "Nothing to skip here.",
            parse_mode="Markdown",
        )


async def back_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Go back one step in multi-step workflows."""
    from app.utils.business_hours import check_business_hours
    if not await check_business_hours(update, context):
        return
    telegram_user = str(update.effective_user.id)
    state = context.user_data.get("state")

    if state == "awaiting_worker_count":
        # Back to contractor selection
        contractor_search = context.bot_data.get("contractor_search")
        if contractor_search:
            all_contractors = contractor_search.get_all_contractors()
            contractor_tuples = [
                (f"{c.name} ({c.type})" if c.type else c.name, str(i))
                for i, c in enumerate(all_contractors)
            ]
            context.user_data["search_contractor_objects"] = list(all_contractors)
            total_pages = max(1, (len(contractor_tuples) + 9) // 10)
            context.user_data["search_results"] = contractor_tuples
            context.user_data["search_page"] = 0
            context.user_data["search_total_pages"] = total_pages
            context.user_data["state"] = "awaiting_contractor_selection"
            keyboard = contractor_selection_keyboard(contractor_tuples, page=0, total_pages=total_pages)
            await update.message.reply_text(
                "Select a contractor:", reply_markup=keyboard, parse_mode="Markdown"
            )
        else:
            context.user_data["state"] = "awaiting_contractor_name"
            await update.message.reply_text("Send the contractor name:")

    elif state == "awaiting_zone":
        # Back to worker count input
        context.user_data["state"] = "awaiting_worker_count"
        contractor_name = context.user_data.get("selected_contractor", "Unknown")
        await update.message.reply_text(
            f"How many workers for {contractor_name}? (enter a number)",
            parse_mode="Markdown",
        )

    elif state == "awaiting_details":
        # Back to zone selection
        context.user_data["state"] = "awaiting_zone"
        contractor_name = context.user_data.get("selected_contractor", "Unknown")
        workers = context.user_data.get("current_workers", 0)
        contractor_search = context.bot_data.get("contractor_search")
        if contractor_search:
            zones = contractor_search.get_all_zones()
            zone_names = [z.name for z in zones]
            keyboard = zone_selection_keyboard(zone_names)
            await update.message.reply_text(
                f"Workers: {workers}\n\nNow select the work zone for {contractor_name}, or skip:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        else:
            context.user_data["current_zone"] = None
            context.user_data["state"] = "awaiting_details"
            await update.message.reply_text(
                f"Workers: {workers}\n\nEnter the work details (column G), or type /skip to leave empty:",
                parse_mode="Markdown",
            )

    else:
        await update.message.reply_text(
            "Nothing to go back from. Use /start for the dashboard.",
            parse_mode="Markdown",
        )

    logger.info("User %s went back from state %s", telegram_user, state)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.utils.business_hours import check_business_hours
    if not await check_business_hours(update, context):
        return
    telegram_user = str(update.effective_user.id)

    try:
        report: Report = context.user_data.get("current_report")
        if report is None:
            await update.message.reply_text(
                "No active report to finish. Start with /new or use /start for the dashboard.",
                parse_mode="Markdown",
            )
            return

        total_workers = sum((item.workers or 0) for item in report.items) if report.items else 0
        contractor_count = len(report.items) if report.items else 0

        # Clear all intermediate state variables
        for key in ("selected_contractor", "selected_contractor_raw_name", "selected_contractor_type",
                     "selected_contractor_code", "current_workers", "current_zone",
                     "search_results", "search_page", "search_total_pages", "search_contractor_objects"):
            context.user_data.pop(key, None)

        context.user_data["state"] = "idle"

        text = (
            f"\U0001f4cb *Report Summary*\n\n"
            f"Date: {report.date}\n"
            f"Contractors: {contractor_count}\n"
            f"Total Workers: {total_workers}"
        )
        keyboard = report_actions_keyboard("draft")
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        logger.info("Report done: user=%s, date=%s, contractors=%d", telegram_user, report.date, contractor_count)

        # Generate and send PDF
        try:
            preview_service = context.bot_data.get("pdf_preview_service")
            if preview_service and report.items:
                pdf_path = preview_service.generate_preview(report)
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=InputFile(Path(pdf_path).read_bytes(), filename=f"Labor_Report_{report.date}.pdf"),
                )
        except Exception as pdf_e:
            logger.warning("Could not send PDF after /done: %s", pdf_e)
    except Exception as e:
        logger.error("Error finishing report for user %s: %s", telegram_user, e)
        await update.message.reply_text("An error occurred while finishing the report.")


async def cancel_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_user = str(update.effective_user.id)

    context.user_data.clear()
    logger.info("Report cancelled by user %s", telegram_user)

    # Redirect to dashboard
    from app.bot.handlers.start import dashboard_callback
    await dashboard_callback(update, context)


async def handle_contractor_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_user = str(update.effective_user.id)

    try:
        page = int(query.data.replace("contractor_page:", ""))
        search_results: list[tuple[str, str]] = context.user_data.get("search_results", [])
        total_pages = context.user_data.get("search_total_pages", 1)

        context.user_data["search_page"] = page
        keyboard = contractor_selection_keyboard(search_results, page=page, total_pages=total_pages)

        await query.edit_message_text("Select a contractor:", reply_markup=keyboard, parse_mode="Markdown")
        logger.info("Contractor page changed to %d (user=%s)", page, telegram_user)
    except Exception as e:
        logger.error("Error changing contractor page for user %s: %s", telegram_user, e)
        await query.edit_message_text("An error occurred. Please try again.")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")

    if state in ("awaiting_contractor_name", "awaiting_contractor_selection"):
        await handle_contractor_name(update, context)
    elif state == "awaiting_worker_count":
        await handle_worker_count(update, context)
    elif state == "awaiting_details":
        await handle_details_input(update, context)
    elif state == "awaiting_search_query":
        # Forward to search handler (imported here to avoid circular dependency)
        from app.bot.handlers.search import handle_search_query
        await handle_search_query(update, context)
    elif state == "awaiting_contractor_name_for_report":
        # Forward to contractor report handler
        from app.bot.handlers.start import handle_contractor_report_name
        await handle_contractor_report_name(update, context)
    elif state in ("awaiting_hr_amount", "awaiting_hr_reason",
                   "awaiting_hr_trip_date", "awaiting_hr_report_ref",
                   "awaiting_reject_note", "awaiting_delegate_target",
                   "awaiting_deduction_month",
                   "awaiting_lmo_reason", "awaiting_lmo_start",
                   "awaiting_lmo_end", "awaiting_lmo_date",
                   "awaiting_lmo_hours", "awaiting_att_note",
                   "awaiting_case_summary", "awaiting_case_note",
                   "awaiting_case_appeal", "awaiting_disc_note",
                   "awaiting_disc_appeal"):
        # Forward to HR handlers (imported here to avoid circular dependency)
        from app.bot.handlers import hr as hr_handlers

        if state == "awaiting_hr_amount":
            await hr_handlers.handle_hr_amount(update, context)
        elif state == "awaiting_hr_reason":
            await hr_handlers.handle_hr_reason(update, context)
        elif state == "awaiting_hr_trip_date":
            await hr_handlers.handle_hr_trip_date(update, context)
        elif state == "awaiting_hr_report_ref":
            await hr_handlers.handle_hr_report_ref(update, context)
        elif state == "awaiting_reject_note":
            await hr_handlers.handle_reject_note(update, context)
        elif state == "awaiting_delegate_target":
            await hr_handlers.handle_delegate_target(update, context)
        elif state == "awaiting_deduction_month":
            await hr_handlers.handle_deduction_month(update, context)
        elif state == "awaiting_lmo_reason":
            await hr_handlers.handle_lmo_reason(update, context)
        elif state == "awaiting_lmo_start":
            await hr_handlers.handle_lmo_start(update, context)
        elif state == "awaiting_lmo_end":
            await hr_handlers.handle_lmo_end(update, context)
        elif state == "awaiting_lmo_date":
            await hr_handlers.handle_lmo_date(update, context)
        elif state == "awaiting_lmo_hours":
            await hr_handlers.handle_lmo_hours(update, context)
        elif state == "awaiting_att_note":
            from app.bot.handlers import attendance as att_handlers

            await att_handlers.handle_att_note(update, context)
        elif state == "awaiting_case_summary":
            from app.bot.handlers import cases as case_handlers

            await case_handlers.handle_case_summary(update, context)
        elif state == "awaiting_case_note":
            from app.bot.handlers import cases as case_handlers

            await case_handlers.handle_case_note(update, context)
        elif state == "awaiting_case_appeal":
            from app.bot.handlers import cases as case_handlers

            await case_handlers.handle_case_appeal(update, context)
        elif state == "awaiting_disc_note":
            from app.bot.handlers import discipline as disc_handlers

            await disc_handlers.handle_disc_note(update, context)
        elif state == "awaiting_disc_appeal":
            from app.bot.handlers import discipline as disc_handlers

            await disc_handlers.handle_disc_appeal(update, context)
    elif state in ("awaiting_finalize_confirmation", "awaiting_lock_confirmation"):
        await update.message.reply_text(
            "Please use the confirmation buttons above, or type /cancel to go back.",
            parse_mode="Markdown",
        )
    elif state is None:
        # No active state — check if message looks like a date (PDF request)
        from app.bot.handlers.report_retrieval import handle_date_text
        handled = await handle_date_text(update, context)
        if not handled:
            # Not a date — ignore silently
            pass
    else:
        logger.debug("Unhandled text message in state: %s", state)
        await update.message.reply_text(
            "I'm not sure what to do with that. Use /help to see available commands.",
            parse_mode="Markdown",
        )


def get_registration_handlers() -> list:
    return [
        CommandHandler("new", new_report_command),
        CommandHandler("copy", copy_yesterday_command),
        CommandHandler("done", done_command),
        CommandHandler("back", back_command),
        CommandHandler("skip", skip_command),
        CallbackQueryHandler(handle_contractor_selection, pattern="^select_contractor:"),
        CallbackQueryHandler(handle_contractor_page, pattern="^contractor_page:"),
        CallbackQueryHandler(handle_zone_selection, pattern="^select_zone:"),
        CallbackQueryHandler(cancel_report, pattern="^cancel_report$"),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message),
    ]
