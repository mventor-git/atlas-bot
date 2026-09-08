"""
Admin user management handlers for Labor-Report.

Commands and button-based flows for managing users:
- /users — List all users with approve/reject buttons for pending users
- /approve <chat_id> — Quick approve a pending user
- /reject <chat_id> — Quick reject a pending user
- /promote <chat_id> — Promote user to admin (superadmin only)
- /demote <chat_id> — Demote admin to normal_user (superadmin only)
- /notify [message] — Broadcast notification to all approved users

All operations are restricted to admin+ roles.
Pending users see the "waiting for approval" message from start.py.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from app.services.authorization_service import AuthorizationService
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ─── User Management Callback (from admin panel) ───────────────


async def handle_admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin_users callback from admin panel (show pending users with actions)."""
    query = update.callback_query
    await query.answer()

    auth: AuthorizationService = context.bot_data["authorization_service"]
    admin_chat_id = str(update.effective_user.id)

    if not auth.can_manage_users(admin_chat_id):
        await query.edit_message_text("Unauthorized. Only admins can manage users.")
        return

    await _show_pending_users(update, context, query)


async def _show_pending_users(update, context, query) -> None:
    """Show pending users with approve/reject buttons."""
    auth: AuthorizationService = context.bot_data["authorization_service"]
    pending_users = auth.get_pending_users()

    if not pending_users:
        await query.edit_message_text(
            "No pending users awaiting approval.\n\n"
            "Use the Role Manager GUI to manage all users.",
        )
        return

    text = f"Pending Users ({len(pending_users)}):\n\n"
    keyboard = []

    for user in pending_users:
        name = user.first_name or user.username or "Unknown"
        chat_id_display = user.chat_id
        text += f"• `{chat_id_display}` — {name}\n"
        # Add inline approve/reject buttons for each pending user
        keyboard.append([
            InlineKeyboardButton(f"Approve {name}", callback_data=f"approve_user:{user.chat_id}"),
            InlineKeyboardButton(f"Reject {name}", callback_data=f"reject_user:{user.chat_id}"),
        ])

    keyboard.append([InlineKeyboardButton("Refresh", callback_data="admin_users")])
    from app.bot.keyboards import admin_keyboard
    role = auth.get_role(str(update.effective_user.id))
    keyboard.append([InlineKeyboardButton("Back to Admin Panel", callback_data="admin_panel")])
    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def handle_approve_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve a pending user via callback button."""
    query = update.callback_query
    await query.answer()

    auth: AuthorizationService = context.bot_data["authorization_service"]
    admin_chat_id = str(update.effective_user.id)

    # Double-check admin authorization
    if not auth.can_manage_users(admin_chat_id):
        await query.edit_message_text("Unauthorized. Only admins can approve users.")
        return

    target_chat_id = query.data.replace("approve_user:", "")

    user = auth.get_user(target_chat_id)
    if user is None:
        await query.edit_message_text(f"User {target_chat_id} not found.")
        return

    if user.role != "pending":
        await query.edit_message_text(
            f"User {target_chat_id} is already {user.role}, not pending."
        )
        return

    auth.approve_user(target_chat_id, admin_chat_id)
    logger.info("Admin %s approved user %s via callback", admin_chat_id, target_chat_id)

    # Notify the approved user
    try:
        await context.bot.send_message(
            chat_id=int(target_chat_id),
            text=(
                "Your request to use the Labor Report bot has been approved!\n\n"
                "Use /start to begin."
            ),
        )
    except Exception as e:
        logger.warning("Could not notify approved user %s: %s", target_chat_id, e)

    # Show updated list
    await _show_pending_users(update, context, query)


async def handle_reject_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reject a pending user via callback button."""
    query = update.callback_query
    await query.answer()

    auth: AuthorizationService = context.bot_data["authorization_service"]
    admin_chat_id = str(update.effective_user.id)

    # Double-check admin authorization
    if not auth.can_manage_users(admin_chat_id):
        await query.edit_message_text("Unauthorized. Only admins can reject users.")
        return

    target_chat_id = query.data.replace("reject_user:", "")

    user = auth.get_user(target_chat_id)
    if user is None:
        await query.edit_message_text(f"User {target_chat_id} not found.")
        return

    if user.role != "pending":
        await query.edit_message_text(
            f"User {target_chat_id} is already {user.role}, not pending."
        )
        return

    auth.reject_user(target_chat_id, admin_chat_id)
    logger.info("Admin %s rejected user %s via callback", admin_chat_id, target_chat_id)

    # Show updated list
    await _show_pending_users(update, context, query)


# ─── Command Handlers ──────────────────────────────────────────


async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve a pending user (admin+ only)."""
    auth: AuthorizationService = context.bot_data["authorization_service"]
    chat_id = str(update.effective_user.id)

    if not auth.can_manage_users(chat_id):
        await update.message.reply_text("Unauthorized. Only admins can approve users.")
        return

    target = _extract_chat_id(update, context)
    if target is None:
        # Show pending users instead
        pending = auth.get_pending_users()
        if not pending:
            await update.message.reply_text("No pending users.")
            return
        text = "Pending users:\n"
        for u in pending:
            name = u.first_name or u.username or "Unknown"
            text += f"`{u.chat_id}` — {name}\n"
        text += "\nUse /approve <chat_id> to approve a specific user."
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    user = auth.get_user(target)
    if user is None:
        await update.message.reply_text(f"User {target} not found.")
        return

    if user.role != "pending":
        await update.message.reply_text(
            f"User {target} is already {user.role}, not pending."
        )
        return

    auth.approve_user(target, chat_id)
    await update.message.reply_text(f"User {target} approved. They can now use the bot.")
    logger.info("Admin %s approved user %s", chat_id, target)

    # Notify the approved user
    try:
        await context.bot.send_message(
            chat_id=int(target),
            text=(
                "Your request to use the Labor Report bot has been approved!\n\n"
                "Use /start to begin."
            ),
        )
    except Exception as e:
        logger.warning("Could not notify approved user %s: %s", target, e)


async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reject a pending user (admin+ only)."""
    auth: AuthorizationService = context.bot_data["authorization_service"]
    chat_id = str(update.effective_user.id)

    if not auth.can_manage_users(chat_id):
        await update.message.reply_text("Unauthorized. Only admins can reject users.")
        return

    target = _extract_chat_id(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: /reject <chat_id>\n"
            "Example: /reject 123456789"
        )
        return

    user = auth.get_user(target)
    if user is None:
        await update.message.reply_text(f"User {target} not found.")
        return

    if user.role != "pending":
        await update.message.reply_text(
            f"User {target} is already {user.role}, not pending."
        )
        return

    auth.reject_user(target, chat_id)
    await update.message.reply_text(f"User {target} rejected.")
    logger.info("Admin %s rejected user %s", chat_id, target)


async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Promote a user to admin (superadmin / project_manager only)."""
    auth: AuthorizationService = context.bot_data["authorization_service"]
    chat_id = str(update.effective_user.id)

    if not auth.can_promote_demote(chat_id):
        await update.message.reply_text(
            "Unauthorized. Only the superadmin or project manager can promote users to admin."
        )
        return

    target = _extract_chat_id(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: /promote <chat_id>\n"
            "Example: /promote 123456789"
        )
        return

    if target == chat_id:
        await update.message.reply_text("You are already the superadmin.")
        return

    user = auth.get_user(target)
    if user is None:
        await update.message.reply_text(f"User {target} not found.")
        return

    if user.role in ("admin", "project_manager", "executive_engineer"):
        await update.message.reply_text(f"User {target} is already {user.role}.")
        return

    if user.role == "superadmin":
        await update.message.reply_text("Cannot promote the superadmin.")
        return

    auth.promote_to_admin(target, chat_id)
    await update.message.reply_text(f"User {target} promoted to admin.")
    logger.info("Superadmin %s promoted user %s to admin", chat_id, target)


async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Demote an admin to normal_user (superadmin / project_manager only)."""
    auth: AuthorizationService = context.bot_data["authorization_service"]
    chat_id = str(update.effective_user.id)

    if not auth.can_promote_demote(chat_id):
        await update.message.reply_text(
            "Unauthorized. Only the superadmin or project manager can demote admins."
        )
        return

    target = _extract_chat_id(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: /demote <chat_id>\n"
            "Example: /demote 123456789"
        )
        return

    if target == chat_id:
        await update.message.reply_text("Cannot demote the superadmin.")
        return

    user = auth.get_user(target)
    if user is None:
        await update.message.reply_text(f"User {target} not found.")
        return

    if user.role not in ("admin", "project_manager", "executive_engineer"):
        await update.message.reply_text(
            f"User {target} is {user.role}, not an admin. Nothing to demote."
        )
        return

    auth.demote_to_user(target, chat_id)
    await update.message.reply_text(f"User {target} demoted to normal user.")
    logger.info("Superadmin %s demoted user %s to normal_user", chat_id, target)


async def notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a notification to all approved users (admin+ only).

    Usage: /notify <message>

    Sends the given message to every approved user (not pending/rejected).
    If no message is provided, sends a default reminder to create reports.
    """
    auth: AuthorizationService = context.bot_data["authorization_service"]
    chat_id = str(update.effective_user.id)

    if not auth.can_manage_users(chat_id):
        await update.message.reply_text("Unauthorized. Only admins can send notifications.")
        return

    # Build message from args or use default
    if context.args and len(" ".join(context.args).strip()) > 0:
        custom_message = " ".join(context.args).strip()
        message = (
            f"Notification from Admin\n\n"
            f"{custom_message}"
        )
    else:
        message = (
            "Reminder to Submit Report\n\n"
            "Please ensure your labor report for today is created and submitted.\n\n"
            "Use /new to start a new report.\n"
            "Use /copy to copy yesterday's report.\n"
            "Use /start for the dashboard.\n\n"
            "Thank you!"
        )

    # Get all approved user chat IDs
    target_ids = auth.get_all_approved_chat_ids()
    if not target_ids:
        await update.message.reply_text("No approved users to notify.")
        return

    # Use NotificationManager if available, otherwise send directly
    from app.services.notification_manager import NotificationManager
    nm: NotificationManager | None = context.bot_data.get("notification_manager")

    if nm is not None:
        sent, total = await nm.send_broadcast(message, target_ids)
    else:
        sent = 0
        total = len(target_ids)
        for cid in target_ids:
            try:
                await context.bot.send_message(
                    chat_id=int(cid),
                    text=message,
                    parse_mode="Markdown",
                )
                sent += 1
            except Exception as e:
                logger.warning("Failed to send notify to %s: %s", cid, e)

    await update.message.reply_text(
        f"Notification sent to {sent}/{total} user(s)."
    )
    logger.info(
        "Admin %s sent broadcast notification to %d/%d users",
        chat_id, sent, total,
    )


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all registered users (admin+ only).

    Shows pending users with inline approve/reject buttons.
    """
    auth: AuthorizationService = context.bot_data["authorization_service"]
    chat_id = str(update.effective_user.id)

    if not auth.can_manage_users(chat_id):
        await update.message.reply_text("Unauthorized. Only admins can view the user list.")
        return

    users = auth.get_all_users()

    if not users:
        await update.message.reply_text("No users registered yet.")
        return

    lines = ["Registered Users:\n"]
    for u in users:
        name = u.first_name or u.username or "—"
        status = _role_emoji(u.role)
        lines.append(
            f"{status} `{u.chat_id}` — {name}\n"
            f"   Role: {u.role} | Since: {u.created_at[:10]}"
        )

    # Split into multiple messages if too long
    text = "\n".join(lines)
    if len(text) > 4000:
        for i in range(0, len(text), 3500):
            await update.message.reply_text(
                text[i:i + 3500], parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


# --- Helpers ---


def _extract_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Extract a chat ID from command args or reply."""
    if context.args and len(context.args) > 0:
        raw = context.args[0].strip()
        if raw.startswith("@"):
            return raw[1:]
        return raw
    return None


def _role_emoji(role: str) -> str:
    """Get an emoji for a role."""
    return {
        "superadmin": "Crown",
        "project_manager": "Briefcase",
        "executive_engineer": "HardHat",
        "admin": "Star",
        "normal_user": "Check",
        "viewer": "Eye",
        "pending": "Hourglass",
        "rejected": "Cross",
    }.get(role, "?")


# --- Registration ---


def get_registration_handlers() -> list:
    return [
        CommandHandler("approve", approve_command),
        CommandHandler("reject", reject_command),
        CommandHandler("promote", promote_command),
        CommandHandler("demote", demote_command),
        CommandHandler("notify", notify_command),
        CommandHandler("users", users_command),
        CallbackQueryHandler(handle_admin_users_callback, pattern="^admin_users$"),
        CallbackQueryHandler(handle_approve_user_callback, pattern="^approve_user:"),
        CallbackQueryHandler(handle_reject_user_callback, pattern="^reject_user:"),
    ]
