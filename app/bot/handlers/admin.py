"""Admin command handlers for Labor-Report Telegram bot."""

from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from app.repositories.report_repository import ReportRepository
from app.services.add_contractor_service import AddContractorService
from app.services.audit_service import AuditService
from app.services.event_log_service import EventLogService
from app.services.report_workflow_service import ReportWorkflowService
from app.bot.keyboards import admin_keyboard
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _get_auth(context):
    """Get authorization service from bot_data."""
    return context.bot_data.get("authorization_service")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the admin panel."""
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.is_admin(telegram_user):
        await update.message.reply_text("Unauthorized. You are not an admin.")
        return

    role = auth.get_role(telegram_user)
    await update.message.reply_text(
        "Admin Panel", reply_markup=admin_keyboard(role=role), parse_mode="Markdown"
    )


async def admin_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent event log entries."""
    query = update.callback_query
    await query.answer()
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.is_admin(telegram_user):
        await query.edit_message_text("Unauthorized.")
        return

    event_log: EventLogService = context.bot_data.get("event_log_service")
    if event_log is None:
        await query.edit_message_text("Event log is not available.")
        return

    try:
        events = event_log.get_recent(limit=10)
        if not events:
            await query.edit_message_text("No events recorded yet.")
            return

        text = "Recent Events\n\n"
        for e in events:
            ts = e.timestamp[:19] if e.timestamp else "?"
            action = e.action or "?"
            user = e.telegram_user or "?"
            obj = e.object_date or e.object_id or "?"
            text += f"`{ts}` {action} by {user} on {obj}\n"

        role = auth.get_role(telegram_user)
        await query.edit_message_text(
            text, reply_markup=admin_keyboard(role=role), parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Failed to fetch events: %s", e)
        await query.edit_message_text("Failed to fetch events.")


async def admin_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show system health check."""
    query = update.callback_query
    await query.answer()
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.is_admin(telegram_user):
        await query.edit_message_text("Unauthorized.")
        return

    try:
        repo: ReportRepository = context.bot_data["report_repository"]
        all_reports = repo.get_all()
        total = len(all_reports)
        drafts = sum(1 for r in all_reports if r.status and r.status.value == "draft")
        finals = sum(1 for r in all_reports if r.status and r.status.value == "final")
        locked = sum(1 for r in all_reports if r.status and r.status.value == "locked")

        text = (
            "System Health\n\n"
            f"Total Reports: {total}\n"
            f"  Draft: {drafts}\n"
            f"  Final: {finals}\n"
            f"  Locked: {locked}\n"
            f"Server Time: {datetime.now().strftime('%H:%M:%S')}\n"
            f"Status: OK"
        )

        role = auth.get_role(telegram_user)
        await query.edit_message_text(
            text, reply_markup=admin_keyboard(role=role), parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Health check failed: %s", e)
        await query.edit_message_text("Health check failed.")


async def admin_autolock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger auto-lock of expired reports."""
    query = update.callback_query
    await query.answer()
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.is_admin(telegram_user):
        await query.edit_message_text("Unauthorized.")
        return

    try:
        workflow: ReportWorkflowService = context.bot_data["workflow_service"]
        locked = workflow.auto_lock_reports(telegram_user=telegram_user)
        role = auth.get_role(telegram_user)
        # G1: Log auto-lock to audit
        audit: AuditService = context.bot_data.get("audit_service")
        if audit and locked > 0:
            audit._repo.log_locked(
                telegram_user=telegram_user,
                user_role=role,
                report_date="[AUTO-LOCK]",
                report_status="locked",
                details=f"auto-locked {locked} report(s)",
            )
        await query.edit_message_text(
            f"Auto-lock completed. Locked {locked} report(s).",
            reply_markup=admin_keyboard(role=role), parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Auto-lock failed: %s", e)
        await query.edit_message_text("Auto-lock failed.")


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route admin panel callbacks."""
    query = update.callback_query
    data = query.data
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.is_admin(telegram_user):
        await query.answer()
        await query.edit_message_text("Unauthorized.")
        return

    if data == "admin_events":
        await admin_events(update, context)
    elif data == "admin_health":
        await admin_health(update, context)
    elif data == "admin_autolock":
        await admin_autolock(update, context)
    elif data == "admin_users":
        # Forward to admin_users module
        from app.bot.handlers.admin_users import handle_admin_users_callback
        await handle_admin_users_callback(update, context)
    elif data == "admin_role_gui":
        await query.edit_message_text(
            "Use the Role Manager GUI application to assign roles.\n\n"
            "The GUI reads/writes the SQLite database directly.",
        )
    else:
        await query.answer()


async def add_contractor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /add_contractor command — add a new contractor (admin/superadmin only).

    Usage:
        /add_contractor Contractor Name
        /add_contractor Contractor Name --type Civil

    The contractor becomes immediately available in search and suggestions.
    """
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)

    if not auth.is_admin(telegram_user):
        await update.message.reply_text(
            "\u26a0\ufe0f Unauthorized. Only admins can add contractors."
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "\U0001f4cb *Add Contractor*\n\n"
            "Usage:\n"
            "`/add_contractor Contractor Name`\n"
            "`/add_contractor Contractor Name --type Civil`\n\n"
            "The contractor will be available in search immediately.",
            parse_mode="Markdown",
        )
        return

    # Parse name and optional type
    name_parts: list[str] = []
    ctype: str | None = None
    args_iter = iter(args)
    for arg in args_iter:
        if arg == "--type":
            try:
                ctype = next(args_iter)
            except StopIteration:
                await update.message.reply_text(
                    "Missing type value after `--type`.", parse_mode="Markdown"
                )
                return
        else:
            name_parts.append(arg)

    name = " ".join(name_parts).strip()
    if not name:
        await update.message.reply_text("Contractor name cannot be empty.")
        return

    # Get services
    add_svc: AddContractorService = context.bot_data.get("add_contractor_service")
    if add_svc is None:
        await update.message.reply_text("Service unavailable. Please try again later.")
        return

    role = auth.get_role(telegram_user)
    success, message = add_svc.add_contractor(
        name=name,
        added_by=telegram_user,
        user_role=role,
        ctype=ctype,
    )

    await update.message.reply_text(
        f"\u2705 {message}" if success else f"\u274c {message}"
    )


async def notify_pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show pending outbox rows (superadmin trial utility, 031).

    Usage: /notify_pending [site] - totals, or the first 15 rows of a site.
    """
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)
    if auth is None or not auth.is_super_admin(telegram_user):
        await update.message.reply_text("Unauthorized. Superadmin only.")
        return
    outbox = (context.bot_data or {}).get("notification_outbox")
    if outbox is None:
        await update.message.reply_text("Outbox not wired.")
        return
    args = context.args or []
    if not args:
        total = outbox.pending_count()
        await update.message.reply_text(f"Pending notifications: {total}.")
        return
    rows = outbox.pending_for_site(args[0])
    lines = [f"Pending in `{args[0]}`: {len(rows)}"]
    for row in rows[:15]:
        lines.append(f"#{row.id} {row.ntype} -> `{row.recipient}` "
                     f"try {row.attempts}/{row.max_attempts}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def notify_cycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run one outbox dispatch cycle now (superadmin trial utility, 031)."""
    telegram_user = str(update.effective_user.id)
    auth = _get_auth(context)
    if auth is None or not auth.is_super_admin(telegram_user):
        await update.message.reply_text("Unauthorized. Superadmin only.")
        return
    manager = (context.bot_data or {}).get("notification_manager")
    if manager is None:
        await update.message.reply_text("Notification manager not running.")
        return
    report = await manager.run_cycle()
    flat = "; ".join(f"{site}={list(v) if isinstance(v, dict) else v}"
                     for site, v in (report or {}).items())
    await update.message.reply_text(f"Cycle done: {flat or 'no sites'}.")


def get_registration_handlers() -> list:
    return [
        CommandHandler("admin", admin_command),
        CommandHandler("add_contractor", add_contractor_command),
        CommandHandler("notify_pending", notify_pending_command),
        CommandHandler("notify_cycle", notify_cycle_command),
        CallbackQueryHandler(
            handle_admin_callback,
            pattern="^(admin_events|admin_health|admin_autolock|admin_users|admin_role_gui)$",
        ),
    ]
