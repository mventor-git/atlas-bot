"""Register module: employee onboarding lifecycle (bot-first).

REQUEST: /register -> name capture -> HRRequest(register, pending).
CODE: EMP-### via EmployeeRepository.issue_code (UNIQUE-safe, retry).
APPROVE: single admin action sets role + site + membership grant.
UPDATE/DEACTIVATE: edit name/role/site; deactivate keeps history.
RESOLVE/AUDIT: cards use CODE·Name (UNMAPPED + /register hint); every
transition appends to the existing user_activity_log trail.
English-only. Keyboards capped at 3. Chat IDs masked on cards.
AuthZ untouched: request open, admin actions superadmin-only (same as
/hr_member employee).
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from app.bot.keyboards import cap_buttons
from app.bot.vendor_hermes import normalize_telegram_chat_id
from app.models.hr import HRRequestStatus, HRRequestType
from app.services.hr_service import HRService
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _me(update: Update) -> tuple[str, str]:
    user = update.effective_user
    name = f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)
    return str(normalize_telegram_chat_id(user.id)), name


def _auth(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["authorization_service"]


def _hr(context: ContextTypes.DEFAULT_TYPE) -> HRService:
    return context.bot_data["hr_service"]


def _employees(context: ContextTypes.DEFAULT_TYPE):
    try:
        return context.bot_data.get("employee_repo")
    except Exception:
        return None


def _mask(chat_id: str) -> str:
    s = str(chat_id or "")
    return f"***{s[-4:]}" if len(s) > 4 else "***"


def _audit(context, action: str, actor: str, role: str,
           details: str, site: str | None) -> None:
    try:
        svc = context.bot_data.get("audit_service")
        if svc is not None:
            svc.log_event(actor, role or "pending", action,
                          details=details, site_id=site)
    except Exception as e:
        logger.warning("Register audit %s failed: %s", action, e)


def _who(context, chat_id: str) -> str:
    repo = _employees(context)
    if repo is None:
        return "UNMAPPED · limited"
    return repo.display(chat_id)


def _admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    chat_id, _ = _me(update)
    if not _auth(context).is_super_admin(chat_id):
        return None
    return chat_id


def _queue_keyboard(first_id: int | None) -> InlineKeyboardMarkup:
    B = InlineKeyboardButton
    if first_id is None:
        return cap_buttons([B("My Status", callback_data="reg_my")], 3)
    return cap_buttons([
        B(f"Approve #{first_id}", callback_data=f"reg_approve:{first_id}"),
        B(f"Reject #{first_id}", callback_data=f"reg_reject:{first_id}"),
        B("My Status", callback_data="reg_my"),
    ], 3)


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, tg_name = _me(update)
    repo = _employees(context)
    if repo is not None:
        emp = repo.get(chat_id)
        if emp is not None and emp.active:
            await update.effective_message.reply_text(
                f"You are registered as {_who(context, chat_id)}.")
            return
    service = _hr(context)
    try:
        from app.database import driver
        site = driver.site_id()
        dup = [r for r in service._repo.list_for_requester(chat_id, site_id=site)
               if r.request_type == HRRequestType.REGISTER
               and r.status == HRRequestStatus.PENDING]
    except Exception:
        dup = []
    if dup:
        await update.effective_message.reply_text(
            f"Request #{dup[0].id} is pending review.",
            reply_markup=cap_buttons(
                [InlineKeyboardButton("My Status", callback_data="reg_my")], 3))
        return
    context.user_data["state"] = "awaiting_register_name"
    await update.effective_message.reply_text(
        "Send your full name (as it appears on payroll).",
        reply_markup=cap_buttons(
            [InlineKeyboardButton("Cancel", callback_data="reg_cancel")], 3))


async def handle_register_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") != "awaiting_register_name":
        return
    chat_id, tg_name = _me(update)
    full_name = (update.message.text or "").strip()
    if len(full_name) < 2:
        await update.message.reply_text("Send your full name (2+ characters).")
        return
    repo = _employees(context)
    if repo is not None:
        emp = repo.get(chat_id)
        if emp is not None and emp.active:
            context.user_data.pop("state", None)
            await update.message.reply_text(
                f"You are registered as {_who(context, chat_id)}.")
            return
    try:
        req = _hr(context).request_registration(chat_id, full_name)
    except DatabaseError as e:
        context.user_data.pop("state", None)
        await update.message.reply_text(f"Could not file the request: {e}")
        return
    context.user_data.pop("state", None)
    _audit(context, "register_requested", chat_id, "pending",
           f"request #{req.id} name={full_name} from={_mask(chat_id)}",
           req.site_id)
    await update.message.reply_text(
        f"Request #{req.id} filed for {full_name}. An admin will review it.")


async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = _admin_only(update, context)
    if admin is None:
        await update.effective_message.reply_text(
            "Registration review is for the superadmin.")
        return
    rows = _hr(context).pending_registrations()
    if not rows:
        await update.effective_message.reply_text("No pending registrations.")
        return
    lines = []
    for r in rows[:5]:
        lines.append(
            f"#{r.id} {r.requester_name} ({_mask(r.requester_chat_id)}) @ {r.site_id}")
    first = rows[0].id
    await update.effective_message.reply_text(
        "Pending registrations:\n" + "\n".join(lines),
        reply_markup=_queue_keyboard(first))


async def my_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, _ = _me(update)
    service = _hr(context)
    rows = []
    try:
        for site in _auth(context).sites_for_user(chat_id) or []:
            rows.extend(service.visible_to(chat_id, "viewer", site_id=site))
    except Exception:
        pass
    mine = [r for r in rows if str(r.requester_chat_id) == str(chat_id)
            and r.request_type == HRRequestType.REGISTER]
    if not mine:
        # Fall back to direct requester scan (unmapped users have no sites yet).
        try:
            from app.database import driver
            mine = [r for r in service._repo.list_for_requester(
                chat_id, site_id=driver.site_id())
                if r.request_type == HRRequestType.REGISTER]
        except Exception:
            mine = []
    if not mine:
        await update.effective_message.reply_text(
            "No registration request found. Send /register to start.")
        return
    last = sorted(mine, key=lambda r: r.id or 0)[-1]
    extra = f" as {last.note}" if last.status == "approved" else (
        f": {last.note}" if last.note else "")
    await update.effective_message.reply_text(
        f"Request #{last.id} is {last.status}{extra}.")


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit name/role/site in one action.

    Usage: /hr_register_update <chat_id> <role> <site_id> [Full Name...]
    Name omitted keeps the stored name.
    """
    admin = _admin_only(update, context)
    if admin is None:
        await update.message.reply_text("Only the superadmin manages registrations.")
        return
    parts = (update.message.text or "").split()
    if len(parts) < 4:
        await update.message.reply_text(
            "Usage: /hr_register_update <chat_id> <role> <site_id> [Full Name]")
        return
    _, target, role, site_id = parts[:4]
    full_name = " ".join(parts[4:]) or None
    try:
        emp = _hr(context).update_employee(
            target, _employees(context), _auth(context),
            full_name=full_name, role=role, site_id=site_id, changed_by=admin)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not update: {e}")
        return
    _audit(context, "register_updated", admin,
           _auth(context).get_role(admin),
           f"chat={_mask(target)} code={emp.employee_code} role={role} site={site_id}"
           + (f" name={full_name}" if full_name else ""),
           site_id)
    await update.message.reply_text(
        f"Updated {emp.employee_code} · {emp.full_name} ({_mask(target)}).")


async def deactivate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deactivate: keeps history, retires code, suspends memberships."""
    admin = _admin_only(update, context)
    if admin is None:
        await update.message.reply_text("Only the superadmin manages registrations.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.message.reply_text("Usage: /hr_register_deactivate <chat_id>")
        return
    target = parts[1]
    try:
        _hr(context).deactivate_employee(
            target, _employees(context), _auth(context))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not deactivate: {e}")
        return
    _audit(context, "register_deactivated", admin,
           _auth(context).get_role(admin),
           f"chat={_mask(target)} retired (history kept)", None)
    await update.message.reply_text(
        f"Deactivated {_mask(target)}. History kept; code retired.")


async def handle_register_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Approve/reject button entry. Returns True if handled."""
    query = update.callback_query
    data = (query.data or "").strip()
    if data == "reg_cancel":
        context.user_data.pop("state", None)
        await query.edit_message_text("Registration cancelled. Send /register to restart.")
        return True
    if data == "reg_my":
        await query.answer()
        await my_status_command(update, context)
        return True
    if data.startswith("reg_approve:"):
        admin = _admin_only(update, context)
        if admin is None:
            await query.edit_message_text("Registration review is for the superadmin.")
            return True
        try:
            req_id = int(data.split(":")[1])
        except ValueError:
            return True
        context.user_data["reg_approve_id"] = req_id
        context.user_data["state"] = "awaiting_reg_approve_detail"
        await query.edit_message_text(
            f"Approve #{req_id}: send role and site as `<role> <site_id>`.",
            parse_mode="Markdown")
        return True
    if data.startswith("reg_reject:"):
        admin = _admin_only(update, context)
        if admin is None:
            await query.edit_message_text("Registration review is for the superadmin.")
            return True
        try:
            req_id = int(data.split(":")[1])
        except ValueError:
            return True
        context.user_data["reg_reject_id"] = req_id
        context.user_data["state"] = "awaiting_reg_reject_reason"
        await query.edit_message_text(f"Reject #{req_id}: send the reason.")
        return True
    return False


async def handle_approve_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") != "awaiting_reg_approve_detail":
        return
    admin = _admin_only(update, context)
    if admin is None:
        await update.message.reply_text("Only the superadmin approves registrations.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.message.reply_text("Send role and site as `<role> <site_id>`.")
        return
    role, site_id = parts
    req_id = context.user_data.get("reg_approve_id")
    admin_name = _me(update)[1]
    try:
        req = _hr(context).approve_registration(
            req_id, admin, admin_name, role, site_id,
            _employees(context), _auth(context))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not approve: {e}")
        return
    context.user_data.pop("reg_approve_id", None)
    context.user_data.pop("state", None)
    _audit(context, "register_approved", admin,
           _auth(context).get_role(admin),
           f"request #{req.id} -> {req.note} from={_mask(req.requester_chat_id)}",
           req.site_id)
    emp = _employees(context).get(req.requester_chat_id)
    card = f"{emp.employee_code} · {emp.full_name}" if emp else str(req.note)
    await update.message.reply_text(f"Approved #{req.id} as {card}.")


async def handle_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") != "awaiting_reg_reject_reason":
        return
    admin = _admin_only(update, context)
    if admin is None:
        await update.message.reply_text("Only the superadmin rejects registrations.")
        return
    reason = (update.message.text or "").strip()
    req_id = context.user_data.get("reg_reject_id")
    admin_name = _me(update)[1]
    try:
        req = _hr(context).reject_registration(
            req_id, admin, admin_name, note=reason)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not reject: {e}")
        return
    context.user_data.pop("reg_reject_id", None)
    context.user_data.pop("state", None)
    _audit(context, "register_rejected", admin,
           _auth(context).get_role(admin),
           f"request #{req.id} reason={reason} from={_mask(req.requester_chat_id)}",
           req.site_id)
    await update.message.reply_text(f"Rejected #{req.id}: {reason}")


def get_registration_handlers() -> list:
    return [
        CommandHandler("register", register_command),
        CommandHandler("hr_register_queue", queue_command),
        CommandHandler("hr_register_update", update_command),
        CommandHandler("hr_register_deactivate", deactivate_command),
        CommandHandler("reg_my", my_status_command),
        CallbackQueryHandler(handle_register_callback, pattern="^reg_"),
    ]
