from datetime import date, datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from app.services.daily_dashboard_service import DailyDashboardService, DashboardData
from app.bot import site_session
from app.repositories.report_repository import ReportRepository
from app.bot.keyboards import (
    main_menu_keyboard, report_actions_keyboard, confirmation_keyboard,
    contractor_selection_keyboard, contractor_reports_keyboard,
)
from app.services.arabic_date_service import ArabicDateService
from app.services.auto_save_service import AutoSaveService
from app.services.one_click_yesterday_service import OneClickYesterdayService
from app.services.report_workflow_service import ReportWorkflowService
from app.services.authorization_service import AuthorizationService
from app.services.audit_service import AuditService
from app.utils.exceptions import ReportLifecycleError
from app.models.database import Report, ReportStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _get_auth(context: ContextTypes.DEFAULT_TYPE):
    """Get authorization service from bot_data."""
    from app.services.authorization_service import AuthorizationService
    return context.bot_data.get("authorization_service")


def _get_role(context: ContextTypes.DEFAULT_TYPE, chat_id: str) -> str:
    """Get user role from authorization service."""
    auth = _get_auth(context)
    if auth is None:
        return "pending"
    return auth.get_role(chat_id)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.services.authorization_service import AuthorizationService

    telegram_user = str(update.effective_user.id)
    auth: AuthorizationService = context.bot_data["authorization_service"]
    dashboard_service: DailyDashboardService = context.bot_data["daily_dashboard_service"]

    # Register or identify the user on every /start
    user_info = update.effective_user
    auth.register_or_get(
        chat_id=telegram_user,
        username=user_info.username,
        first_name=user_info.first_name,
    )

    # Check authorization
    role = auth.get_role(telegram_user)
    if role == "pending":
        text = (
            "\U0001f6ab *Access Pending*\n\n"
            "Your request to use this bot has been submitted to the admin.\n"
            "Please wait for approval and try again later.\n\n"
            "You will be notified once your access is granted."
        )
        sender = update.callback_query if update.callback_query else update.message
        await sender.reply_text(text, parse_mode="Markdown")

        # Notify super admin about pending user
        _notify_admin_new_user(update, context, telegram_user, auth)
        return

    if role == "rejected":
        sender = update.callback_query if update.callback_query else update.message
        await sender.reply_text(
            "\U0001f6ab *Access Denied*\n\n"
            "Your request to use this bot has been rejected by the admin.",
            parse_mode="Markdown",
        )
        return

    # Authorized — show dashboard
    dash = dashboard_service.get_dashboard(
        today=date.today().isoformat(),
    )
    # Check if any reports exist
    repo: ReportRepository = context.bot_data["report_repository"]
    all_reports = repo.get_all() if repo else []
    has_reports = len(all_reports) > 0

    # Check business hours
    from app.utils.business_hours import is_business_hours
    in_business_hours = is_business_hours()

    text = (
        f"\U0001f4cb *Labor Report Bot*\n\n"
        f"\U0001f4c5 {dash.date} ({dash.day})\n"
        f"\u23f0 {dash.time}\n\n"

        f"*Status:* {dash.report_status.replace('_', ' ').title()}\n"
        f"*Contractors:* {dash.contractor_count}\n"
        f"*Total Workers:* {dash.total_workers}\n"
        f"*Time Remaining:* {dash.time_remaining if dash.time_remaining else 'N/A'}"
    )

    keyboard = main_menu_keyboard(dash.report_status, role=role, has_reports=has_reports, is_business_hours=in_business_hours)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


def _notify_admin_new_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    auth: "AuthorizationService",
) -> None:
    """Notify all admins and superadmins about a new pending user registration."""
    from app.services.notification_manager import NotificationManager

    admin_ids = auth.get_all_admin_chat_ids()
    user_info = update.effective_user
    name = user_info.first_name or user_info.username or chat_id
    username = f"@{user_info.username}" if user_info.username else "—"

    message = (
        f"\U0001f4cb *New User Registration*\n\n"
        f"A new user has registered and is awaiting approval.\n\n"
        f"*Name:* {name}\n"
        f"*Username:* {username}\n"
        f"*Chat ID:* `{chat_id}`\n\n"
        f"Use /approve {chat_id} to approve or /reject {chat_id} to reject."
    )

    nm: NotificationManager | None = context.bot_data.get("notification_manager")

    import asyncio
    async def _send_to_all_admins():
        for admin_id in admin_ids:
            try:
                if nm is not None:
                    await asyncio.wait_for(
                        nm.send_admin_notification(admin_id, message),
                        timeout=10.0,
                    )
                else:
                    await asyncio.wait_for(
                        context.bot.send_message(
                            chat_id=int(admin_id),
                            text=message,
                            parse_mode="Markdown",
                        ),
                        timeout=10.0,
                    )
            except asyncio.TimeoutError:
                logger.warning("Admin notification timed out for admin %s (new user %s)", admin_id, chat_id)
            except Exception as e:
                logger.error("Failed to notify admin %s about new user %s: %s", admin_id, chat_id, e)

    asyncio.ensure_future(_send_to_all_admins())
    logger.info("Queued notification to %d admins for new user %s", len(admin_ids), chat_id)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = str(update.effective_user.id)
    role = _get_role(context, telegram_user)
    auth = _get_auth(context)

    text = (
        "*Labor Report Bot Commands*\n\n"
        "Use the buttons below to navigate:\n\n"
        "📝 *Create Report* - Start a new daily report\n"
        "📋 *Copy Yesterday* - Copy yesterday's report\n"
        "📂 *Open Draft* - Edit today's draft\n"
        "👁 *View Report* - See today's report\n"
        "📄 *Download PDF* - Get today's PDF\n"
        "🔍 *Search Reports* - Find reports by date, contractor\n"
        "📊 *Contractor Reports* - Get automated reports\n"
        "⚙️ *Admin Panel* - Admin functions\n\n"

        "*Quick Tips:*\n"
        "• Just type a date like `12-07-2026` or `yesterday` to get the PDF\n"
        "• Use /start to return to the dashboard\n"
        "• All features are accessible via the buttons"
    )

    # Handle both message and callback query
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        logger.warning("help_command called without message or callback_query")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel current operation and return to dashboard."""
    telegram_user = str(update.effective_user.id)
    context.user_data.clear()
    logger.info("User %s cancelled current operation", telegram_user)
    await start_command(update, context)


async def fresh_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete today's report and start completely fresh."""
    telegram_user = str(update.effective_user.id)
    role = _get_role(context, telegram_user)
    auth = _get_auth(context)

    if not auth.can_create_reports(telegram_user):
        await update.message.reply_text("You don't have permission to delete reports.")
        return

    today = date.today().isoformat()

    try:
        site = site_session.resolve_site(update, context)
        if site is None:
            await site_session.ask_site(update, context, resume="fresh",
                                        hint="Press /fresh again.")
            return
        repo: ReportRepository = context.bot_data["report_repository"]
        report = repo.get_by_date(today, site_id=site)

        if report is None:
            await update.message.reply_text(
                "\U0001f9f9 No report found for today. You're already fresh!\n\n"
                "Use /new to create a new report.",
                parse_mode="Markdown",
            )
            return

        if report.is_locked:
            await update.message.reply_text(
                "\U0001f6ab Today's report is *locked* and cannot be deleted.\n"
                "Ask an admin to unlock it first.",
                parse_mode="Markdown",
            )
            return

        repo.delete(report.id)
        context.user_data.clear()
        logger.info("User %s deleted today's report for fresh start", telegram_user)

        await update.message.reply_text(
            "\u2705 *Fresh Start!* Today's report has been deleted.\n\n"
            "Use /start for the dashboard.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Fresh start failed for user %s: %s", telegram_user, e)
        await update.message.reply_text("Failed to reset. Please try again.")

async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = str(update.effective_user.id)
    role = _get_role(context, telegram_user)
    dashboard_service: DailyDashboardService = context.bot_data["daily_dashboard_service"]

    # Check business hours
    from app.utils.business_hours import is_business_hours
    in_business_hours = is_business_hours()

    dash = dashboard_service.get_dashboard(
        today=date.today().isoformat(),
    )

    repo: ReportRepository = context.bot_data["report_repository"]
    all_reports = repo.get_all() if repo else []
    has_reports = len(all_reports) > 0

    text = (
        f"\U0001f4cb *Labor Report Bot*\n\n"
        f"\U0001f4c5 {dash.date} ({dash.day})\n"
        f"\u23f0 {dash.time}\n\n"

        f"*Status:* {dash.report_status.replace('_', ' ').title()}\n"
        f"*Contractors:* {dash.contractor_count}\n"
        f"*Total Workers:* {dash.total_workers}\n"
        f"*Time Remaining:* {dash.time_remaining if dash.time_remaining else 'N/A'}"
    )

    keyboard = main_menu_keyboard(dash.report_status, role=role, has_reports=has_reports, is_business_hours=in_business_hours)

    # Handle "Message is not modified" error gracefully
    try:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except Exception as e:
        if "Message is not modified" in str(e):
            # Message is already correct, just answer the callback
            await update.callback_query.answer()
        else:
            # Re-raise other errors
            raise


async def _handle_revert_last(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               query, repo, today: str, telegram_user: str) -> None:
    """Revert the last contractor entry added to today's draft."""
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="revert",
                                    hint="Press the button again.")
        return
    report = repo.get_by_date(today, site_id=site)
    if report is None:
        await query.edit_message_text("No report found for today.")
        return
    if not report.is_draft:
        await query.edit_message_text(f"Cannot revert: report is *{report.status.value}*.", parse_mode="Markdown")
        return
    if not report.items or len(report.items) == 0:
        await query.edit_message_text("Nothing to revert — the report has no entries.")
        return

    # Remove the last item
    last_item = report.items[-1]
    report.items = report.items[:-1]
    repo.update(report)
    logger.info("User %s reverted last contractor '%s' from draft %s", telegram_user, last_item.contractor, today)

    # Audit log the revert
    try:
        audit: AuditService = context.bot_data.get("audit_service")
        if audit:
            auth: AuthorizationService = context.bot_data.get("authorization_service")
            role = auth.get_role(telegram_user) if auth else "unknown"
            audit.log_reverted(telegram_user, role, report, contractor_name=last_item.contractor)
    except Exception as audit_e:
        logger.warning("Failed to audit log revert: %s", audit_e)

    # Show updated draft
    total_workers = sum((i.workers or 0) for i in report.items)
    text = (
        f"Reverted last entry: *{last_item.contractor}*\n\n"
        f"*Updated Draft*\n"
        f"Contractors: {len(report.items)}\n"
        f"Total Workers: {total_workers}"
    )
    if report.items:
        items_text = ""
        for i, item in enumerate(report.items, 1):
            w = item.workers or 0
            items_text += f"\n{i}. {item.contractor}: {w} workers"
        text += items_text

    role = _get_role(context, telegram_user)
    keyboard = report_actions_keyboard("draft", role=role)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _handle_revert_report(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 query, repo, today: str, telegram_user: str) -> None:
    """Revert the entire report for today (admin only)."""
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="revert",
                                    hint="Press the button again.")
        return
    report = repo.get_by_date(today, site_id=site)
    if report is None:
        await query.edit_message_text("No report found for today.")
        return
    if not report.is_draft:
        await query.edit_message_text(f"Cannot revert: report is *{report.status.value}*.", parse_mode="Markdown")
        return
    if not report.items or len(report.items) == 0:
        await query.edit_message_text("Nothing to revert — the report has no entries.")
        return

    # Delete the report entirely
    repo.delete(report.id)
    logger.info("Admin %s reverted full report for %s", telegram_user, today)

    # Audit log the full report revert
    try:
        audit: AuditService = context.bot_data.get("audit_service")
        if audit:
            auth: AuthorizationService = context.bot_data.get("authorization_service")
            role = auth.get_role(telegram_user) if auth else "unknown"
            audit.log_reverted(telegram_user, role, report, contractor_name="[FULL_REPORT]")
    except Exception as audit_e:
        logger.warning("Failed to audit log full revert: %s", audit_e)

    await query.edit_message_text(
        "Full report reverted. Today's report has been deleted.\n\n"
        "Use /start to start fresh.",
        parse_mode="Markdown",
    )


async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data

    # Gracefully handle expired callback queries
    try:
        await query.answer()
    except Exception:
        logger.debug("Callback query expired: %s", data)

    telegram_user = str(update.effective_user.id)
    role = _get_role(context, telegram_user)
    auth = _get_auth(context)

    # Quarantine: misconfigured auth denies everything but help;
    # pending/rejected users see nothing but help.
    if auth is None:
        if data != "help":
            await query.edit_message_text("Authorization not available. Please try again later.")
            return
    elif role in ("pending", "rejected", "unknown") and data != "help":
        from app.bot.handlers._authz import PENDING_MSG, DENIED

        await query.edit_message_text(
            PENDING_MSG if role == "pending" else DENIED)
        return

    # Guard: if no auth service is available, deny all non-read operations
    if auth is None and data not in ("dashboard", "view_report", "search", "help", "contractor_reports"):
        await query.edit_message_text("Authorization not available. Please try again later.")
        return

    # Check business hours for edit operations
    from app.utils.business_hours import check_business_hours
    edit_operations = {"create_report", "copy_yesterday", "open_draft", "finalize", "lock", "unlock", "add_contractor"}
    if data in edit_operations:
        if not auth.can_create_reports(telegram_user) and data != "unlock":
            await query.edit_message_text("You don't have permission to edit reports.")
            return
        if not await check_business_hours(update, context, callback_data=data):
            return

    repo: ReportRepository = context.bot_data.get("report_repository")
    if repo is None:
        await query.edit_message_text("Report repository not available.")
        return
    today = date.today().isoformat()
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=f"menu:{data}",
                                    hint="Press the button again.")
        return

    if data == "dashboard":
        await dashboard_callback(update, context)

    elif data == "create_report":
        if not auth.can_create_reports(telegram_user):
            await query.edit_message_text("You don't have permission to create reports.")
            return

        day_name = ArabicDateService.get_day_name(date.today())

        auto_save_service = context.bot_data.get("auto_save_service")
        if auto_save_service:
            report = Report(
                date=today,
                day=day_name,
                telegram_user=telegram_user,
                status=ReportStatus.DRAFT,
            )
            report = auto_save_service.save_draft(report, telegram_user)
            context.user_data["current_report"] = report

        context.user_data["state"] = "awaiting_contractor_selection"

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
            keyboard = contractor_selection_keyboard(contractor_tuples, page=0, total_pages=total_pages, page_size=10)
            await query.edit_message_text(
                f"\U0001f4dd *Create Report*\n\nSelect a contractor from the list below "
                f"({len(all_contractors)} contractors available):",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                "\U0001f4dd *Create Report*\n\nSend me the contractor name, or use /done to finish.",
                parse_mode="Markdown",
            )

        logger.info("User %s started report creation flow", update.effective_user.id)

    elif data == "copy_yesterday":
        if not auth.can_create_reports(telegram_user):
            await query.edit_message_text("You don't have permission to create reports.")
            return

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        day_name = ArabicDateService.get_day_name(date.today())

        try:
            one_click_service: OneClickYesterdayService = context.bot_data["one_click_yesterday_service"]
            auto_save_service: AutoSaveService = context.bot_data["auto_save_service"]

            draft = one_click_service.copy_yesterday(
                yesterday_date=yesterday,
                today_date=today,
                today_day=day_name,
                telegram_user=telegram_user,
            )

            if draft is None:
                await query.edit_message_text(
                    "\U0001f4cb No report from yesterday to copy.\n\n"
                    "Start fresh with the Create Report button.",
                    parse_mode="Markdown",
                )
                return

            draft = auto_save_service.save_draft(draft, telegram_user)
            context.user_data["current_report"] = draft
            context.user_data["state"] = "idle"

            items_text = ""
            total_workers = 0
            for item in draft.items:
                w = item.workers or 0
                total_workers += w
                zone_text = f" [{item.zone}]" if item.zone else ""
                items_text += f"\n\u2022 {item.contractor}{zone_text}: {w} workers"

            text = (
                f"\U0001f4cb *Report Copied from Yesterday*\n\n"
                f"Date: {today}\n"
                f"Source: {yesterday}\n"
                f"Contractors: {len(draft.items)}\n"
                f"Total Workers: {total_workers}\n"
                f"{items_text}"
            )
            keyboard = report_actions_keyboard("draft", role=role)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
            logger.info("Yesterday report copied for user %s", telegram_user)

            # Auto-generate PDF
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
            logger.error("Copy yesterday failed for user %s: %s", telegram_user, e)
            await query.edit_message_text("Failed to copy yesterday's report.")

    elif data == "open_draft":
        if not auth.can_create_reports(telegram_user):
            await query.edit_message_text("You don't have permission to edit reports.")
            return

        try:
            report = repo.get_by_date(today, site_id=site)

            if report is None:
                await query.edit_message_text(
                    "\U0001f4cb No draft found for today.\n\nCreate one with the Create Report button.",
                    parse_mode="Markdown",
                )
                return

            if not report.is_draft:
                await query.edit_message_text(
                    f"\U0001f4cb Today's report is *{report.status.value}* (not a draft).",
                    parse_mode="Markdown",
                )
                return

            context.user_data["current_report"] = report
            context.user_data["state"] = "idle"
            items_text = ""
            total_workers = 0
            for i, item in enumerate(report.items, 1):
                w = item.workers or 0
                total_workers += w
                zone_text = f" [{item.zone}]" if item.zone else ""
                detail_text = f" — {item.details}" if item.details else ""
                items_text += f"\n{i}. {item.contractor}{zone_text}: {w} workers{detail_text}"

            text = (
                f"\U0001f4cb *Draft Report*\n\n"
                f"Date: {report.day} {report.date}\n"
                f"Contractors: {len(report.items)}\n"
                f"Total Workers: {total_workers}\n"
                f"{items_text}"
            )
            keyboard = report_actions_keyboard("draft", role=role)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
            logger.info("Draft loaded for user %s", telegram_user)
        except Exception as e:
            logger.error("Open draft failed for user %s: %s", telegram_user, e)
            await query.edit_message_text("Failed to load draft.")

    elif data in ("preview_pdf", "download_pdf"):
        try:
            report = repo.get_by_date(today, site_id=site)

            if report is None or not report.items:
                await query.edit_message_text(
                    "\U0001f4c4 No report data available.",
                    parse_mode="Markdown",
                )
                return

            from app.services import report_visibility as visibility

            if visibility.resolve(auth, telegram_user, report.site_id) == visibility.OWNER:
                await query.edit_message_text(
                    visibility.render_simple(report)
                    + "\n\n_PDF download needs reviewer access._",
                    parse_mode="Markdown",
                )
                return

            preview_service = context.bot_data.get("pdf_preview_service")
            if preview_service:
                pdf_path = preview_service.generate_preview(report)
                if report.is_draft:
                    repo.update(report)

                # Audit log the export
                try:
                    audit: AuditService = context.bot_data.get("audit_service")
                    if audit:
                        auth_obj: AuthorizationService = context.bot_data.get("authorization_service")
                        role = auth_obj.get_role(telegram_user) if auth_obj else "unknown"
                        audit.log_exported(telegram_user, role, report)
                except Exception as audit_e:
                    logger.warning("Failed to audit log export: %s", audit_e)

                await query.edit_message_text(
                    "\U0001f4c4 *PDF Generated*\n\nSending PDF...",
                    parse_mode="Markdown",
                )
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=InputFile(Path(pdf_path).read_bytes(), filename=f"Labor_Report_{today}.pdf"),
                )
                logger.info("PDF sent for %s: %s", today, pdf_path)
            else:
                await query.edit_message_text(
                    "\U0001f4c4 PDF generation requires Excel to be installed.",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error("PDF generation failed for user %s: %s", telegram_user, e)
            await query.edit_message_text("Failed to generate PDF.")

    elif data == "finalize":
        if not auth.can_finalize(telegram_user):
            await query.edit_message_text("You don't have permission to finalize reports.")
            return

        report = repo.get_by_date(today, site_id=site)

        if report is None:
            await query.edit_message_text("No report found for today.", parse_mode="Markdown")
        elif not report.is_draft:
            await query.edit_message_text(
                f"Report is already *{report.status.value}*. Cannot finalize again.",
                parse_mode="Markdown",
            )
        elif not report.items:
            await query.edit_message_text(
                "Cannot finalize an empty report. Add contractors first.",
                parse_mode="Markdown",
            )
        else:
            context.user_data["current_report"] = report
            context.user_data["state"] = "awaiting_finalize_confirmation"
            await query.edit_message_text(
                "\U0001f4cb *Finalize Report*\n\n"
                f"This will lock the report as final.\n"
                f"Contractors: {len(report.items)}\n"
                f"Workers: {sum((i.workers or 0) for i in report.items)}\n\n"
                f"Are you sure?",
                reply_markup=confirmation_keyboard("finalize"),
                parse_mode="Markdown",
            )
        logger.info("User %s triggered finalize flow", update.effective_user.id)

    elif data == "lock":
        if not auth.can_finalize(telegram_user):
            await query.edit_message_text("You don't have permission to lock reports.")
            return

        report = repo.get_by_date(today, site_id=site)

        if report is None:
            await query.edit_message_text("No report found for today.", parse_mode="Markdown")
        elif not report.is_approved:
            await query.edit_message_text(
                f"Report must be *approved* before locking (currently: *{report.status.value}*).\n"
                f"Ask a reviewer to /approve it first.",
                parse_mode="Markdown",
            )
        else:
            context.user_data["current_report"] = report
            context.user_data["state"] = "awaiting_lock_confirmation"
            await query.edit_message_text(
                "\U0001f512 *Lock Report*\n\n"
                f"Contractors: {len(report.items)}\n"
                f"Workers: {sum((i.workers or 0) for i in report.items)}\n\n"
                f"Locking makes the report permanently read-only. Are you sure?",
                reply_markup=confirmation_keyboard("lock"),
                parse_mode="Markdown",
            )
        logger.info("User %s triggered lock flow", update.effective_user.id)

    elif data == "unlock":
        if not auth.is_admin(telegram_user):
            await query.edit_message_text("Unauthorized. Only admins can unlock reports.", parse_mode="Markdown")
            return
        await unlock_via_callback(update, context)

    elif data == "approve_report":
        gated = await _review_gate(update, context)
        if gated is None:
            if site_session.resolve_site(update, context) is not None:
                await query.edit_message_text("Approval needs a reviewer grant.")
            return
        chat_id, site = gated
        report = repo.get_by_date(today, site_id=site)
        if report is None:
            await query.edit_message_text("No report found for today.")
            return
        workflow: ReportWorkflowService = context.bot_data["workflow_service"]
        try:
            report = workflow.approve_report(report, chat_id)
        except ReportLifecycleError as e:
            await query.edit_message_text(f"Could not approve: {e}")
            return
        await query.edit_message_text(
            f"\u2705 *Report Approved*\n\nLock it with /lock.",
            reply_markup=report_actions_keyboard(report.status.value,
                                                 role=role),
            parse_mode="Markdown",
        )
        await _report_notice(context, "report_approved", report.telegram_user,
                             site, report.date,
                             reference=f"review:{report.date}")
    elif data == "reject_report":
        gated = await _review_gate(update, context)
        if gated is None:
            if site_session.resolve_site(update, context) is not None:
                await query.edit_message_text("Rejection needs a reviewer grant.")
            return
        chat_id, site = gated
        report = repo.get_by_date(today, site_id=site)
        if report is None:
            await query.edit_message_text("No report found for today.")
            return
        if not report.is_final:
            await query.edit_message_text(
                f"Only a *final* report can be rejected (currently: *{report.status.value}*).",
                parse_mode="Markdown",
            )
            return
        context.user_data["report_reject_id"] = report.id
        context.user_data["report_reject_site"] = site
        context.user_data["state"] = "awaiting_report_reject_note"
        await query.edit_message_text("Send the rejection note as text.")
    elif data == "resubmit_report":
        chat_id = telegram_user
        site = site_session.resolve_site(update, context)
        if site is None:
            await site_session.ask_site(update, context, resume="menu:resubmit",
                                        hint="Press the button again.")
            return
        report = repo.get_by_date(today, site_id=site)
        if report is None:
            await query.edit_message_text("No report found for today.")
            return
        if str(report.telegram_user) != chat_id and not auth.has_capability(
                chat_id, "approve_daily_report", site):
            await query.edit_message_text("Only the creator or a reviewer can resubmit.")
            return
        workflow = context.bot_data["workflow_service"]
        try:
            report = workflow.resubmit_report(report, chat_id)
        except ReportLifecycleError as e:
            await query.edit_message_text(f"Could not resubmit: {e}")
            return
        await query.edit_message_text(
            f"\U0001f4cb *Report Resubmitted*\n\nEdit it, then /finalize again.",
            reply_markup=report_actions_keyboard(report.status.value, role=role),
            parse_mode="Markdown",
        )
        await _notify_reviewers(context, site, "report_resubmitted", report.date)

    elif data == "view_report":
        try:
            report = repo.get_by_date(today, site_id=site)

            if report is None:
                await query.edit_message_text(
                    "\U0001f4ca No report found for today.",
                    parse_mode="Markdown",
                )
                return

            items_text = ""
            total_workers = 0
            for i, item in enumerate(report.items, 1):
                w = item.workers or 0
                total_workers += w
                zone_text = f" [{item.zone}]" if item.zone else ""
                detail_text = f" — {item.details}" if item.details else ""
                items_text += f"\n{i}. {item.contractor}{zone_text}: {w} workers{detail_text}"

            status_icon = "\U0001f512" if report.is_locked else "\u2705" if report.is_final else "\U0001f4cb"
            text = (
                f"{status_icon} *Report: {report.date}*\n\n"
                f"Day: {report.day}\n"
                f"Status: *{report.status.value}*\n"
                f"Contractors: {len(report.items)}\n"
                f"Total Workers: {total_workers}\n"
                f"{items_text}"
            )
            if report.finalized_at:
                text += f"\n\nFinalized: {report.finalized_at[:10]}"
            if report.locked_at:
                text += f"\nLocked: {report.locked_at[:10]}"

            # Audit log the view
            try:
                audit: AuditService = context.bot_data.get("audit_service")
                if audit:
                    auth_obj: AuthorizationService = context.bot_data.get("authorization_service")
                    role_str = auth_obj.get_role(telegram_user) if auth_obj else "unknown"
                    audit.log_viewed(telegram_user, role_str, report)
            except Exception as audit_e:
                logger.warning("Failed to audit log view: %s", audit_e)

            keyboard = report_actions_keyboard(report.status.value, role=role)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
            logger.info("Report viewed by user %s", telegram_user)
        except Exception as e:
            logger.error("View report failed for user %s: %s", telegram_user, e)
            await query.edit_message_text("Failed to load report.")

    elif data == "search":
        context.user_data["state"] = "awaiting_search_query"
        await query.edit_message_text(
            "\U0001f50d *Search Reports*\n\nSend a date (YYYY-MM-DD), contractor name, or keyword to search.",
            parse_mode="Markdown",
        )
        logger.info("User %s triggered search flow", update.effective_user.id)

    elif data == "contractor_reports":
        await query.edit_message_text(
            "\U0001f4ca *Contractor Reports*\n\nSelect a period for the report.\n"
            "Then type the contractor name to get an automated PDF report.",
            reply_markup=contractor_reports_keyboard(),
            parse_mode="Markdown",
        )
        context.user_data["state"] = "awaiting_report_period"
        context.user_data.pop("contractor_report_period", None)
        logger.info("User %s opened contractor reports", telegram_user)

    elif data == "help":
        await help_command(update, context)

    elif data == "admin_panel":
        from app.bot.keyboards import admin_keyboard
        await query.edit_message_text(
            "*Admin Panel*", reply_markup=admin_keyboard(role=role), parse_mode="Markdown"
        )
        logger.info("User %s opened admin panel", telegram_user)

    elif data == "revert_last":
        if not auth.can_create_reports(telegram_user):
            await query.edit_message_text("You don't have permission to edit reports.")
            return
        await _handle_revert_last(update, context, query, repo, today, telegram_user)

    elif data == "revert_report":
        if not auth.is_admin(telegram_user):
            await query.edit_message_text("Only admins can revert the full report.")
            return
        await _handle_revert_report(update, context, query, repo, today, telegram_user)

    elif data == "add_contractor":
        if not auth.can_create_reports(telegram_user):
            await query.edit_message_text("You don't have permission to edit reports.")
            return
        # Forward to create_report flow
        context.user_data["state"] = "awaiting_contractor_selection"
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
            keyboard = contractor_selection_keyboard(contractor_tuples, page=0, total_pages=total_pages)
            await query.edit_message_text(
                "Select a contractor:", reply_markup=keyboard, parse_mode="Markdown"
            )

    else:
        logger.warning("Unknown main menu callback: %s from user %s", data, update.effective_user.id)


async def handle_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle confirmation/rejection of finalize, lock, and other actions."""
    query = update.callback_query
    data = query.data

    try:
        await query.answer()
    except Exception:
        logger.debug("Confirmation callback query expired or invalid: %s", data)

    telegram_user = str(update.effective_user.id)
    role = _get_role(context, telegram_user)
    auth = _get_auth(context)

    from app.utils.business_hours import check_business_hours
    if not await check_business_hours(update, context, callback_data=data):
        return

    if data.startswith("confirm:"):
        action = data.replace("confirm:", "")
        repo: ReportRepository = context.bot_data["report_repository"]
        today = date.today().isoformat()
        site = site_session.resolve_site(update, context)
        if site is None:
            await site_session.ask_site(update, context, resume=f"confirm:{action}",
                                        hint="Press the button again.")
            return
        report = repo.get_by_date(today, site_id=site)

        if report is None:
            await query.edit_message_text("Report not found. It may have been deleted.")
            return

        try:
            if action == "finalize":
                if not auth.can_finalize(telegram_user):
                    await query.edit_message_text("You don't have permission to finalize reports.")
                    return
                workflow: ReportWorkflowService = context.bot_data["workflow_service"]
                report = workflow.finalize_report(report, telegram_user)
                msg = "Report finalized successfully!"
                _cancel_notices(context, site, f"missing:{today}")

                # Auto-generate and send the PDF
                preview_service = context.bot_data.get("pdf_preview_service")
                if preview_service:
                    try:
                        pdf_path = preview_service.generate_preview(report)
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=InputFile(Path(pdf_path).read_bytes(), filename=f"Labor_Report_{today}.pdf"),
                        )
                    except Exception as pdf_e:
                        logger.error("Failed to send PDF after finalize: %s", pdf_e)

            elif action == "lock":
                if not auth.can_finalize(telegram_user):
                    await query.edit_message_text("You don't have permission to lock reports.")
                    return
                workflow = context.bot_data["workflow_service"]
                report = workflow.lock_report(report, telegram_user)
                # G1: Log lock to audit
                audit: AuditService = context.bot_data.get("audit_service")
                if audit:
                    audit.log_locked(telegram_user, role, report)
                msg = "Report locked successfully!"
            else:
                await query.edit_message_text(f"Unknown action: {action}")
                return

            total_workers = sum((i.workers or 0) for i in report.items) if report.items else 0
            text = (
                f"\u2705 *{msg}*\n\n"
                f"Date: {report.date}\n"
                f"Contractors: {len(report.items)}\n"
                f"Total Workers: {total_workers}"
            )
            keyboard = report_actions_keyboard(report.status.value, role=role)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
            logger.info("Action %s confirmed by user %s", action, telegram_user)
        except Exception as e:
            logger.error("Action %s failed for user %s: %s", action, telegram_user, e)
            await query.edit_message_text(f"Failed to {action} the report: {e}")

    elif data.startswith("cancel:"):
        action = data.replace("cancel:", "")
        logger.info("Action %s cancelled by user %s", action, telegram_user)
        context.user_data.clear()
        await dashboard_callback(update, context)


async def unlock_via_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unlock via callback from locked report view (admin only)."""
    query = update.callback_query
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.is_admin(telegram_user):
        await query.edit_message_text(
            "Unauthorized. Only admins can unlock reports.",
            parse_mode="Markdown",
        )
        return

    role = _get_role(context, telegram_user)

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="unlock",
                                    hint="Press the button again.")
        return
    today = date.today().isoformat()
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_date(today, site_id=site)

    if report is None:
        await query.edit_message_text("No report found for today.", parse_mode="Markdown")
    elif not report.is_locked:
        await query.edit_message_text(
            f"Report is not locked (currently: *{report.status.value}*).",
            parse_mode="Markdown",
        )
    else:
        workflow: ReportWorkflowService = context.bot_data["workflow_service"]
        report = workflow.unlock_report(report, telegram_user)
        # G1: Log unlock to audit
        audit: AuditService = context.bot_data.get("audit_service")
        if audit:
            audit.log_unlocked(telegram_user, role, report)
        await query.edit_message_text(
            "\U0001f513 *Report Unlocked*\n\n"
            f"Date: {report.date}\n"
            f"Status: *{report.status.value}*\n\n"
            f"You can now edit the draft.",
            reply_markup=report_actions_keyboard(report.status.value, role=role),
            parse_mode="Markdown",
        )
        logger.info("Report unlocked via callback by admin %s", telegram_user)


async def handle_contractor_report_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle contractor report period selection callback."""
    query = update.callback_query
    await query.answer()

    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    from app.bot import site_session
    from app.services import report_visibility as visibility

    chat_id = str(update.effective_user.id)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="contractor",
                                    hint="Pick a site, then start again.")
        return
    auth = context.bot_data.get("authorization_service")
    if visibility.resolve(auth, chat_id, site) == visibility.OWNER:
        await query.edit_message_text(
            "Contractor history needs reviewer access.")
        return
    telegram_user = str(update.effective_user.id)
    period = query.data.replace("report_period:", "")

    period_labels = {
        "this_week": "This Week",
        "last_week": "Last Week",
        "this_month": "This Month",
        "last_month": "Last Month",
    }
    label = period_labels.get(period, period)

    context.user_data["contractor_report_period"] = period
    
    # Get contractor list for selection
    contractor_search = context.bot_data.get("contractor_search")
    if contractor_search:
        all_contractors = contractor_search.get_all_contractors()
        if all_contractors:
            # Extract names from Contractor objects for the keyboard
            contractor_names = [c.name for c in all_contractors]
            # Store in user_data so callback handler can look up by index
            context.user_data["report_contractor_list"] = contractor_names
            from app.bot.keyboards import contractor_selection_keyboard_for_report
            keyboard = contractor_selection_keyboard_for_report(contractor_names)
            await query.edit_message_text(
                f"\U0001f4ca *Contractor Report — {label}*\n\n"
                f"Select a contractor from the list below:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            logger.info("User %s selected contractor report period: %s (showing %d contractors)", 
                      telegram_user, period, len(contractor_names))
            return
    
    # Fallback: ask for name if no contractors found
    context.user_data["state"] = "awaiting_contractor_name_for_report"
    await query.edit_message_text(
        f"\U0001f4ca *Contractor Report — {label}*\n\n"
        f"Type the contractor name to generate a PDF report.\n\n"
        f"You can send a partial name and I'll search.",
        parse_mode="Markdown",
    )
    logger.info("User %s selected contractor report period: %s (fallback to text input)", telegram_user, period)


async def handle_report_contractor_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle contractor selection from inline keyboard in contractor reports flow."""
    query = update.callback_query
    await query.answer()

    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    telegram_user = str(update.effective_user.id)

    contractor_idx_str = query.data.replace("rc:", "")
    period = context.user_data.get("contractor_report_period", "")

    if not contractor_idx_str or not period:
        await query.edit_message_text("Session expired. Please start again with /start.")
        return

    # Look up the contractor name from the stored list by index
    contractor_list = context.user_data.get("report_contractor_list", [])
    try:
        contractor_idx = int(contractor_idx_str)
        contractor_name = contractor_list[contractor_idx] if 0 <= contractor_idx < len(contractor_list) else ""
    except (ValueError, IndexError):
        contractor_name = ""

    if not contractor_name:
        await query.edit_message_text("Contractor not found. Please try again.")
        return

    period_labels = {
        "this_week": "This Week",
        "last_week": "Last Week",
        "this_month": "This Month",
        "last_month": "Last Month",
    }
    label = period_labels.get(period, period)

    from datetime import datetime, timedelta
    from app.repositories.report_repository import ReportRepository

    # Calculate date range based on period
    now = datetime.now()
    today = now.date()
    weekday = today.weekday()  # Monday=0, Sunday=6
    # Saturday = 5 in Python weekday, Sunday = 6
    days_since_saturday = (weekday + 2) % 7  # Saturday=5 -> 0, Sunday=6 -> 1, ..., Friday=4 -> 6
    this_week_start = today - timedelta(days=days_since_saturday)
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = this_week_start - timedelta(days=1)
    this_month_start = today.replace(day=1)
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)
    last_month_end = this_month_start - timedelta(days=1)

    period_ranges = {
        "this_week": (this_week_start, today),
        "last_week": (last_week_start, last_week_end),
        "this_month": (this_month_start, today),
        "last_month": (last_month_start, last_month_end),
    }
    date_range = period_ranges.get(period)
    if not date_range:
        await query.edit_message_text(f"Unknown period: {period}")
        return

    start_date, end_date = date_range
    repo: ReportRepository = context.bot_data["report_repository"]

    try:
        # Collect ALL matching entries (not just one per day)
        entries = []
        current = start_date
        while current <= end_date:
            report = repo.get_by_date(current.isoformat(), site_id=site)
            if report and report.items:
                for item in report.items:
                    if item.contractor and contractor_name.lower() in item.contractor.lower():
                        entries.append((report, item))
            current += timedelta(days=1)

        if not entries:
            await query.edit_message_text(
                f"No entries found for *{contractor_name}* in {label}.",
                parse_mode="Markdown",
            )
            return

        # Show "generating" message
        await query.edit_message_text(
            f"\U0001f4ca *Contractor Report: {contractor_name}*\n"
            f"Period: {start_date} to {end_date}\n\n"
            f"Generating consolidated PDF with {len(entries)} entries...",
            parse_mode="Markdown",
        )

        # Build entry data for the template
        auth = context.bot_data.get("authorization_service")
        template_entries = []
        total_workers = 0
        for report_obj, item in entries:
            w = item.workers or 0
            total_workers += w

            # Look up who added this data
            added_by_str = "system"
            user_role = "system"
            if auth and report_obj.telegram_user:
                try:
                    role = auth.get_role(str(report_obj.telegram_user))
                    user_role = role or "unknown"
                    user_info = auth.get_user(str(report_obj.telegram_user))
                    if user_info:
                        display_name = user_info.first_name or user_info.username or str(report_obj.telegram_user)
                        added_by_str = f"{display_name} ({role})"
                    else:
                        added_by_str = str(report_obj.telegram_user)
                except Exception:
                    added_by_str = str(report_obj.telegram_user)
                    user_role = "unknown"

            template_entries.append({
                "date": report_obj.date,
                "workers": w,
                "zone": item.zone or "",
                "details": item.details or "",
                "added_by": added_by_str,
                "role": user_role,
            })

        # Generate PDF using the contractor report template
        from app.libre.contractor_report import ContractorReportFiller
        from app.libre.pdf import PDFGenerator

        config = context.bot_data["app_config"]
        filler = ContractorReportFiller(config.contractor_report_template_path)

        # Generate a unique output filename
        safe_name = contractor_name.replace(" ", "_").replace("/", "_")
        doc_output = config.docs_folder_path / f"Contractor_{safe_name}_{start_date}_to_{end_date}.ods"
        pdf_output = config.pdf_folder_path / f"Contractor_{safe_name}_{start_date}_to_{end_date}.pdf"

        filler.fill(
            contractor_name=contractor_name,
            start_date=str(start_date),
            end_date=str(end_date),
            entries=template_entries,
            output_path=doc_output,
        )

        # Convert to PDF
        pdf_gen = PDFGenerator(config)
        pdf_gen.convert_to_pdf(str(doc_output), str(pdf_output))

        # Send the consolidated PDF
        from telegram import InputFile
        if pdf_output.exists():
            with open(pdf_output, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=InputFile(f, filename=f"Contractor_{safe_name}_{start_date}_to_{end_date}.pdf"),
                    caption=(
                        f"\U0001f4ca *{contractor_name}*\n"
                        f"Period: {start_date} to {end_date}\n"
                        f"Entries: {len(entries)} | Total Workers: {total_workers}"
                    ),
                    parse_mode="Markdown",
                )
            pdf_sent = True
        else:
            pdf_sent = False

        # Clean up the intermediate document file
        try:
            doc_output.unlink()
        except Exception:
            pass

        if not pdf_sent:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Failed to generate PDF. Please try again.",
            )

        logger.info(
            "Contractor report generated for user %s: contractor=%s, period=%s, entries=%d, workers=%d, pdf_sent=%s",
            telegram_user, contractor_name, period, len(entries), total_workers, pdf_sent,
        )
    except Exception as e:
        logger.error("Contractor report failed for user %s: %s", telegram_user, e, exc_info=True)
        await query.edit_message_text("Failed to generate contractor report. Please try again.")


async def handle_contractor_report_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle contractor name input for contractor reports PDF generation."""
    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        context.user_data.pop("state", None)
        return
    from app.bot import site_session
    from app.services import report_visibility as visibility

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="contractor",
                                    hint="Type the name again.")
        return
    auth = context.bot_data.get("authorization_service")
    if visibility.resolve(auth, str(update.effective_user.id), site) == visibility.OWNER:
        await update.message.reply_text(
            "Contractor history needs reviewer access.")
        context.user_data.pop("state", None)
        return
    telegram_user = str(update.effective_user.id)
    period = context.user_data.get("contractor_report_period", "")
    contractor_name = update.message.text.strip()

    if not contractor_name:
        await update.message.reply_text("Please enter a valid contractor name.")
        return

    if not period:
        await update.message.reply_text("No period selected. Please start again from Contractor Reports.")
        context.user_data.pop("state", None)
        return

    # Calculate date range from period
    from datetime import timedelta
    today = date.today()
    period_ranges = {
        "this_week": (today - timedelta(days=today.weekday()), today),
        "last_week": (today - timedelta(days=today.weekday() + 7), today - timedelta(days=today.weekday() + 1)),
        "this_month": (today.replace(day=1), today),
        "last_month": (
            (today.replace(day=1) - timedelta(days=1)).replace(day=1),
            today.replace(day=1) - timedelta(days=1),
        ),
    }
    date_range = period_ranges.get(period)
    if not date_range:
        await update.message.reply_text(f"Unknown period: {period}")
        return

    start_date, end_date = date_range
    repo: ReportRepository = context.bot_data["report_repository"]

    # Search for reports containing this contractor in the date range
    try:
        # Collect ALL matching entries (not just one per day)
        entries = []
        current = start_date
        while current <= end_date:
            report = repo.get_by_date(current.isoformat(), site_id=site)
            if report and report.items:
                for item in report.items:
                    if item.contractor and contractor_name.lower() in item.contractor.lower():
                        entries.append((report, item))
            current += timedelta(days=1)

        if not entries:
            await update.message.reply_text(
                f"No entries found for *{contractor_name}* in the selected period.",
                parse_mode="Markdown",
            )
            context.user_data.pop("state", None)
            context.user_data.pop("contractor_report_period", None)
            return

        # Show a "generating" message
        await update.message.reply_text(
            f"\U0001f4ca Generating consolidated report for *{contractor_name}*...\n"
            f"Period: {start_date} to {end_date}\n"
            f"Found {len(entries)} entries.",
            parse_mode="Markdown",
        )

        # Build entry data for the template
        auth = context.bot_data.get("authorization_service")
        template_entries = []
        total_workers = 0
        for report_obj, item in entries:
            w = item.workers or 0
            total_workers += w

            # Look up who added this data
            added_by_str = "system"
            user_role = "system"
            if auth and report_obj.telegram_user:
                try:
                    role = auth.get_role(str(report_obj.telegram_user))
                    user_role = role or "unknown"
                    user_info = auth.get_user(str(report_obj.telegram_user))
                    if user_info:
                        display_name = user_info.first_name or user_info.username or str(report_obj.telegram_user)
                        added_by_str = f"{display_name} ({role})"
                    else:
                        added_by_str = str(report_obj.telegram_user)
                except Exception:
                    added_by_str = str(report_obj.telegram_user)
                    user_role = "unknown"

            template_entries.append({
                "date": report_obj.date,
                "workers": w,
                "zone": item.zone or "",
                "details": item.details or "",
                "added_by": added_by_str,
                "role": user_role,
            })

        # Generate PDF using the contractor report template
        from app.libre.contractor_report import ContractorReportFiller
        from app.libre.pdf import PDFGenerator
        from telegram import InputFile

        config = context.bot_data["app_config"]
        filler = ContractorReportFiller(config.contractor_report_template_path)

        # Generate output files
        safe_name = contractor_name.replace(" ", "_").replace("/", "_")
        doc_output = config.docs_folder_path / f"Contractor_{safe_name}_{start_date}_to_{end_date}.ods"
        pdf_output = config.pdf_folder_path / f"Contractor_{safe_name}_{start_date}_to_{end_date}.pdf"

        filler.fill(
            contractor_name=contractor_name,
            start_date=str(start_date),
            end_date=str(end_date),
            entries=template_entries,
            output_path=doc_output,
        )

        # Convert to PDF
        pdf_gen = PDFGenerator(config)
        pdf_gen.convert_to_pdf(str(doc_output), str(pdf_output))

        # Send the consolidated PDF
        if pdf_output.exists():
            with open(pdf_output, "rb") as f:
                await update.message.reply_document(
                    document=InputFile(f, filename=f"Contractor_{safe_name}_{start_date}_to_{end_date}.pdf"),
                    caption=(
                        f"\U0001f4ca *{contractor_name}*\n"
                        f"Period: {start_date} to {end_date}\n"
                        f"Entries: {len(entries)} | Total Workers: {total_workers}"
                    ),
                    parse_mode="Markdown",
                )
        else:
            await update.message.reply_text("Failed to generate PDF. Please try again.")

        # Clean up the intermediate document file
        try:
            doc_output.unlink()
        except Exception:
            pass

        logger.info(
            "Contractor report generated for user %s: contractor=%s, period=%s, entries=%d, workers=%d",
            telegram_user, contractor_name, period, len(entries), total_workers,
        )
    except Exception as e:
        logger.error("Contractor report failed for user %s: %s", telegram_user, e, exc_info=True)
        await update.message.reply_text("Failed to generate contractor report. Please try again.")
    finally:
        context.user_data.pop("state", None)
        context.user_data.pop("contractor_report_period", None)


async def view_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /view command - view today's report at the active site."""
    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    telegram_user = str(update.effective_user.id)
    role = _get_role(context, telegram_user)
    auth = _get_auth(context)

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="view",
                                    hint="Press /view again.")
        return
    today = date.today().isoformat()

    try:
        repo: ReportRepository = context.bot_data["report_repository"]
        report = repo.get_by_date(today, site_id=site)

        if report is None:
            await update.message.reply_text(
                "\U0001f4cb No report found for today.\n\n"
                "Use /start for the dashboard.",
                parse_mode="Markdown",
            )
            return

        from app.services import report_visibility as visibility

        audience = visibility.resolve(auth, telegram_user, report.site_id)
        if audience == visibility.NONE:
            await update.message.reply_text(
                "You don't have access to this site's reports.")
            return
        if audience == visibility.OWNER:
            if not visibility.owner_may_see_status(
                    report.status.value if report.status else None):
                await update.message.reply_text(
                    "This report is not yet available.")
                return
            await update.message.reply_text(
                visibility.render_simple(report), parse_mode="Markdown")
            logger.info("Simple report viewed via /view by user %s", telegram_user)
            return

        items_text = ""
        total_workers = 0
        for i, item in enumerate(report.items, 1):
            w = item.workers or 0
            total_workers += w
            zone_text = f" [{item.zone}]" if item.zone else ""
            detail_text = f" — {item.details}" if item.details else ""
            items_text += f"\n{i}. {item.contractor}{zone_text}: {w} workers{detail_text}"

        status_icon = "\U0001f512" if report.is_locked else "\u2705" if report.is_final else "\U0001f4cb"
        if report.status == ReportStatus.APPROVED:
            status_icon = "\u2705"
        elif report.status == ReportStatus.REJECTED:
            status_icon = "\U0001f501"
        text = (
            f"{status_icon} *Report: {report.date}* (`{site}`)\n\n"
            f"Day: {report.day}\n"
            f"Status: *{report.status.value}*\n"
            f"Contractors: {len(report.items)}\n"
            f"Total Workers: {total_workers}\n"
            f"{items_text}"
        )
        if report.finalized_at:
            text += f"\n\nFinalized: {report.finalized_at[:10]}"
        if report.approved_by:
            text += f"\nApproved by: `{report.approved_by}`"
        if report.status == ReportStatus.REJECTED:
            text += f"\nRejected by: `{report.rejected_by or '?'}`"
            text += f"\nReason: {report.reject_note or '-'}"
            text += "\nResubmit with /resubmit after fixing."
        if report.locked_at:
            text += f"\nLocked: {report.locked_at[:10]}"

        keyboard = report_actions_keyboard(report.status.value, role=role)
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        logger.info("Report viewed via /view by user %s", telegram_user)
    except Exception as e:
        logger.error("View command failed for user %s: %s", telegram_user, e)
        await update.message.reply_text("Failed to load report.")


async def preview_pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /preview command - generate PDF preview."""
    from app.bot.handlers._authz import require_view

    if not await require_view(update, context):
        return
    telegram_user = str(update.effective_user.id)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="preview",
                                    hint="Press /preview again.")
        return
    today = date.today().isoformat()

    try:
        repo: ReportRepository = context.bot_data["report_repository"]
        report = repo.get_by_date(today, site_id=site)

        if report is None or not report.items:
            await update.message.reply_text(
                "\U0001f4c4 No report data to preview.\n\nAdd contractors first.",
                parse_mode="Markdown",
            )
            return

        from app.services import report_visibility as visibility

        auth = _get_auth(context)
        if visibility.resolve(auth, telegram_user, report.site_id) == visibility.OWNER:
            await update.message.reply_text(
                visibility.render_simple(report)
                + "\n\n_PDF download needs reviewer access._",
                parse_mode="Markdown",
            )
            return

        preview_service = context.bot_data.get("pdf_preview_service")
        if preview_service:
            pdf_path = preview_service.generate_preview(report)
            if report.is_draft:
                repo.update(report)

            await update.message.reply_text(
                "\U0001f4c4 *PDF Generated*\n\nSending PDF...",
                parse_mode="Markdown",
            )
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=InputFile(Path(pdf_path).read_bytes(), filename=f"Labor_Report_{today}.pdf"),
            )
            logger.info("Preview PDF generated and sent for %s: %s", today, pdf_path)
        else:
            await update.message.reply_text(
                "\U0001f4c4 PDF generation requires LibreOffice to be installed.",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error("Preview PDF command failed for user %s: %s", telegram_user, e)
        await update.message.reply_text("Failed to generate preview PDF.")


async def finalize_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /finalize command - finalize current report at the active site."""
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.can_finalize(telegram_user):
        await update.message.reply_text("You don't have permission to finalize reports.")
        return

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="finalize",
                                    hint="Press /finalize again.")
        return
    today = date.today().isoformat()
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_date(today, site_id=site)

    if report is None:
        await update.message.reply_text("No report found for today. Create one first.")
    elif not report.is_draft:
        await update.message.reply_text(
            f"Report is already *{report.status.value}*. Cannot finalize again.",
            parse_mode="Markdown",
        )
    elif not report.items:
        await update.message.reply_text(
            "Cannot finalize an empty report. Add contractors first.",
            parse_mode="Markdown",
        )
    else:
        context.user_data["current_report"] = report
        context.user_data["state"] = "awaiting_finalize_confirmation"
        await update.message.reply_text(
            "\U0001f4cb *Finalize Report*\n\n"
            f"This will lock the report as final.\n"
            f"Contractors: {len(report.items)}\n"
            f"Workers: {sum((i.workers or 0) for i in report.items)}\n\n"
            f"Are you sure?",
            reply_markup=confirmation_keyboard("finalize"),
            parse_mode="Markdown",
        )
    logger.info("User %s triggered finalize via command", update.effective_user.id)


async def lock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /lock command - lock an approved report at the active site."""
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.can_finalize(telegram_user):
        await update.message.reply_text("You don't have permission to lock reports.")
        return

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="lock",
                                    hint="Press /lock again.")
        return
    today = date.today().isoformat()
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_date(today, site_id=site)

    if report is None:
        await update.message.reply_text("No report found for today.")
    elif not report.is_approved:
        await update.message.reply_text(
            f"Report must be *approved* before locking (currently: *{report.status.value}*).\n"
            f"Ask a reviewer to /approve it first.",
            parse_mode="Markdown",
        )
    else:
        context.user_data["current_report"] = report
        context.user_data["state"] = "awaiting_lock_confirmation"
        await update.message.reply_text(
            "\U0001f512 *Lock Report*\n\n"
            f"Contractors: {len(report.items)}\n"
            f"Workers: {sum((i.workers or 0) for i in report.items)}\n\n"
            f"Locking makes the report permanently read-only. Are you sure?",
            reply_markup=confirmation_keyboard("lock"),
            parse_mode="Markdown",
        )
    logger.info("User %s triggered lock via command", update.effective_user.id)


async def unlock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /unlock command - unlock a locked report (admin only)."""
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)
    if not auth.is_admin(telegram_user):
        await update.message.reply_text("Unauthorized. Only admins can unlock reports.")
        return

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="unlock",
                                    hint="Press /unlock again.")
        return
    today = date.today().isoformat()
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_date(today, site_id=site)

    if report is None:
        await update.message.reply_text("No report found for today.")
    elif not report.is_locked:
        await update.message.reply_text(
            f"Report is not locked (currently: *{report.status.value}*).",
            parse_mode="Markdown",
        )
    else:
        workflow: ReportWorkflowService = context.bot_data["workflow_service"]
        report = workflow.unlock_report(report, telegram_user)
        # G1: Log unlock to audit
        role = _get_role(context, telegram_user)
        audit: AuditService = context.bot_data.get("audit_service")
        if audit:
            audit.log_unlocked(telegram_user, role, report)
        await update.message.reply_text(
            "\U0001f513 *Report Unlocked*\n\n"
            f"Date: {report.date}\n"
            f"Status: *{report.status.value}*\n\n"
            f"You can now edit the draft.",
            reply_markup=report_actions_keyboard(report.status.value, role=telegram_user),
            parse_mode="Markdown",
        )
        logger.info("Report unlocked by admin %s", telegram_user)


async def _review_gate(update, context) -> tuple[str, str] | None:
    """Resolve active site + approve_daily_report gate. None = handled."""
    chat_id = str(update.effective_user.id)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="review",
                                    hint="Repeat the review command.")
        return None
    if not _get_auth(context).has_capability(chat_id, "approve_daily_report", site):
        return None
    return chat_id, site


async def _report_notice(context, ntype, recipient, site, date_str, **fields):
    """Durable workflow notice (031); silent when no outbox is wired."""
    from app.bot.notify import notify

    try:
        await notify(context, ntype, recipient, site, date=date_str, **fields)
    except Exception as e:
        logger.warning("Report notice %s failed for %s: %s", ntype, recipient, e)


def _cancel_notices(context, site: str, reference: str) -> None:
    """Supersede stale pending notices after workflow progress."""
    try:
        outbox = (context.bot_data or {}).get("notification_outbox")
        if outbox is not None:
            outbox.cancel_for(site, reference)
    except Exception as e:
        logger.warning("Notice cancel failed for %s/%s: %s", site, reference, e)


async def _notify_reviewers(context, site: str, ntype: str, date_str: str,
                            **fields) -> None:
    """Fan-out to approve_daily_report holders at a site."""
    auth = (context.bot_data or {}).get("authorization_service")
    if auth is None:
        return
    try:
        reviewers = auth.chat_ids_for_site(site, "approve_daily_report")
    except Exception as e:
        logger.warning("Reviewer roster failed for %s: %s", site, e)
        return
    for reviewer in reviewers:
        await _report_notice(context, ntype, reviewer, site, date_str,
                             reference=f"review:{date_str}", priority=1,
                             **fields)


async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve [note] - reviewer accepts today's FINAL report."""
    gated = await _review_gate(update, context)
    if gated is None:
        # Distinguish ask-site (already replied) from denial.
        chat_id = str(update.effective_user.id)
        site = site_session.resolve_site(update, context)
        if site is not None:
            await update.message.reply_text("Approval needs a reviewer grant.")
        return
    chat_id, site = gated
    parts = (update.message.text or "").split(maxsplit=1)
    note = parts[1].strip() if len(parts) > 1 else ""
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_date(date.today().isoformat(), site_id=site)
    if report is None:
        await update.message.reply_text("No report found for today.")
        return
    workflow: ReportWorkflowService = context.bot_data["workflow_service"]
    try:
        report = workflow.approve_report(report, chat_id, note)
    except ReportLifecycleError as e:
        await update.message.reply_text(f"Could not approve: {e}")
        return
    await update.message.reply_text(
        f"\u2705 *Report Approved*\n\nDate: {report.date}\n"
        f"Status: *{report.status.value}*\n\nLock it with /lock.",
        reply_markup=report_actions_keyboard(report.status.value,
                                             role=_get_role(context, chat_id)),
        parse_mode="Markdown",
    )
    await _report_notice(context, "report_approved", report.telegram_user,
                         site, report.date, reference=f"review:{report.date}")


async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reject <note> - reviewer returns today's FINAL report for rework."""
    gated = await _review_gate(update, context)
    if gated is None:
        chat_id = str(update.effective_user.id)
        site = site_session.resolve_site(update, context)
        if site is not None:
            await update.message.reply_text("Rejection needs a reviewer grant.")
        return
    chat_id, site = gated
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text("Usage: /reject <reason note> (note required).")
        return
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_date(date.today().isoformat(), site_id=site)
    if report is None:
        await update.message.reply_text("No report found for today.")
        return
    workflow: ReportWorkflowService = context.bot_data["workflow_service"]
    try:
        report = workflow.reject_report(report, chat_id, parts[1].strip())
    except ReportLifecycleError as e:
        await update.message.reply_text(f"Could not reject: {e}")
        return
    await update.message.reply_text(
        f"\U0001f501 *Report Rejected*\n\nDate: {report.date}\n"
        f"Reason: {report.reject_note}\n\nFix it, then /resubmit.",
        reply_markup=report_actions_keyboard(report.status.value,
                                             role=_get_role(context, chat_id)),
        parse_mode="Markdown",
    )
    await _report_notice(context, "report_rejected", report.telegram_user,
                         site, report.date, reference=f"review:{report.date}",
                         reason=report.reject_note or "")


async def resubmit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resubmit - return a rejected report to draft for rework."""
    chat_id = str(update.effective_user.id)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="resubmit",
                                    hint="Press /resubmit again.")
        return
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_date(date.today().isoformat(), site_id=site)
    if report is None:
        await update.message.reply_text("No report found for today.")
        return
    auth = _get_auth(context)
    if str(report.telegram_user) != chat_id and not auth.has_capability(
            chat_id, "approve_daily_report", site):
        await update.message.reply_text("Only the creator or a reviewer can resubmit.")
        return
    workflow: ReportWorkflowService = context.bot_data["workflow_service"]
    try:
        report = workflow.resubmit_report(report, chat_id)
    except ReportLifecycleError as e:
        await update.message.reply_text(f"Could not resubmit: {e}")
        return
    await update.message.reply_text(
        f"\U0001f4cb *Report Resubmitted*\n\nDate: {report.date}\n"
        f"Status: *{report.status.value}*\n\nEdit it, then /finalize again.",
        reply_markup=report_actions_keyboard(report.status.value,
                                             role=_get_role(context, chat_id)),
        parse_mode="Markdown",
    )
    await _notify_reviewers(context, site, "report_resubmitted", report.date)


async def handle_report_reject_note(update: Update,
                                    context: ContextTypes.DEFAULT_TYPE) -> None:
    """Text handler for the dashboard Reject button's required note."""
    note = (update.message.text or "").strip()
    if not note:
        await update.message.reply_text("Send the rejection note as text.")
        return
    gated = await _review_gate(update, context)
    if gated is None:
        chat_id = str(update.effective_user.id)
        site = site_session.resolve_site(update, context)
        if site is not None:
            await update.message.reply_text("Rejection needs a reviewer grant.")
        return
    chat_id, site = gated
    saved_site = context.user_data.get("report_reject_site", site)
    repo: ReportRepository = context.bot_data["report_repository"]
    report = repo.get_by_id(context.user_data.get("report_reject_id"),
                            site_id=saved_site)
    if report is None:
        await update.message.reply_text("Report not found.")
        return
    workflow: ReportWorkflowService = context.bot_data["workflow_service"]
    try:
        report = workflow.reject_report(report, chat_id, note)
    except ReportLifecycleError as e:
        await update.message.reply_text(f"Could not reject: {e}")
        return
    context.user_data.pop("report_reject_id", None)
    context.user_data.pop("report_reject_site", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"\U0001f501 *Report Rejected*\n\nReason: {report.reject_note}",
        reply_markup=report_actions_keyboard(report.status.value,
                                             role=_get_role(context, chat_id)),
        parse_mode="Markdown",
    )
    await _report_notice(context, "report_rejected", report.telegram_user,
                         saved_site, report.date,
                         reference=f"review:{report.date}",
                         reason=report.reject_note or "")


def get_registration_handlers() -> list:
    return [
        CommandHandler("start", start_command),        CommandHandler("help", help_command),
        CommandHandler("cancel", cancel_command),
        CommandHandler("fresh", fresh_start_command),
        CommandHandler("preview", preview_pdf_command),
        CommandHandler("finalize", finalize_command),
        CommandHandler("lock", lock_command),
        CommandHandler("unlock", unlock_command),
        CommandHandler("view", view_command),
        CommandHandler("approve", approve_command),
        CommandHandler("reject", reject_command),
        CommandHandler("resubmit", resubmit_command),
        CallbackQueryHandler(handle_main_menu_callback, pattern="^(dashboard|create_report|copy_yesterday|open_draft|preview_pdf|download_pdf|finalize|lock|unlock|view_report|search|help|admin_panel|add_contractor|contractor_reports|revert_last|revert_report|approve_report|reject_report|resubmit_report)$"),
        CallbackQueryHandler(handle_confirmation_callback, pattern="^(confirm:|cancel:)"),
        CallbackQueryHandler(handle_contractor_report_period, pattern="^report_period:"),
        CallbackQueryHandler(handle_report_contractor_selection, pattern="^rc:"),
    ]
