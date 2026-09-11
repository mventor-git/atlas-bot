"""Attendance handlers: check-in/out, assisted, confirmer flow (013b)."""

from __future__ import annotations

from datetime import date

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.models.attendance import AttendanceStatus
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


# --- helpers ---

def _me(update: Update) -> tuple[str, str]:
    user = update.effective_user
    name = f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)
    return str(user.id), name


def _auth(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["authorization_service"]


def _attendance(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["attendance_service"]


def _role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id, _ = _me(update)
    return _auth(context).get_role(chat_id)


def _site_config(context: ContextTypes.DEFAULT_TYPE, site_id: str | None = None) -> dict:
    """Site dict for an id (default: this bot's SITE_ID)."""
    from app.database import driver

    sites = getattr(context.bot_data["app_config"], "sites", None) or []
    wanted = site_id or driver.site_id()
    for site in sites:
        if isinstance(site, dict) and str(site.get("id")) == wanted:
            return site
    return sites[0] if sites else {}


def _fence(context: ContextTypes.DEFAULT_TYPE, site_id: str | None = None):
    site = _site_config(context, site_id)
    fence = site.get("geofence") if isinstance(site, dict) else None
    return fence if isinstance(fence, dict) else None


def _resolve_site(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Active site for this user (None -> must pick)."""
    chat_id, _ = _me(update)
    auth = _auth(context)
    resolve = getattr(auth, "resolve_active_site", None)
    if resolve is None:
        from app.database import driver

        return driver.site_id()
    return resolve(chat_id, context.user_data.get("active_site"))


async def _notify(context: ContextTypes.DEFAULT_TYPE, chat_id: str, text: str,
                  reply_markup=None) -> None:
    try:
        await context.bot.send_message(chat_id=int(chat_id), text=text,
                                       reply_markup=reply_markup,
                                       parse_mode="Markdown")
    except Exception as e:
        logger.warning("Could not notify %s: %s", chat_id, e)


def _location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Share location", request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True,
    )


# --- commands ---

async def checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start check-in: ask for a live location share."""
    chat_id, _ = _me(update)
    if not _auth(context).has_capability(chat_id, "check_in"):
        await update.message.reply_text("Check-in needs an approved account.")
        return
    context.user_data["state"] = "awaiting_location_in"
    await update.message.reply_text(
        "Share your live location to check in (or /cancel).",
        reply_markup=_location_keyboard(),
    )


async def checkout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start check-out: ask for a live location share."""
    chat_id, _ = _me(update)
    if not _auth(context).has_capability(chat_id, "check_out"):
        await update.message.reply_text("Check-out needs an approved account.")
        return
    context.user_data["state"] = "awaiting_location_out"
    await update.message.reply_text(
        "Share your live location to check out (or /cancel).",
        reply_markup=_location_keyboard(),
    )


async def assisted_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Proxy check-in: /assisted <target_chat_id> <reason...>."""
    chat_id, name = _me(update)
    if not _auth(context).has_capability(chat_id, "check_in"):
        await update.message.reply_text("Assisted check-in needs an approved account.")
        return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: /assisted <target_chat_id> <reason>")
        return
    _, target, reason = parts
    service = _attendance(context)
    site = _resolve_site(update, context)
    if site is None:
        await _ask_site(update, context, resume="assisted")
        return
    try:
        event = service.assisted_check_in(
            target.strip(), chat_id, date.today().isoformat(), reason,
            site_id=site)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not record: {e}")
        return
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"Assisted check-in recorded for `{target}` (pending verification).",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    await _route_to_confirmer(update, context, event)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a shared location into check-in or check-out."""
    state = context.user_data.get("state")
    if state not in ("awaiting_location_in", "awaiting_location_out"):
        return
    chat_id, _ = _me(update)
    service = _attendance(context)
    site = _resolve_site(update, context)
    if site is None:
        context.user_data["pending_location_action"] = state
        await _ask_site(update, context, resume="location")
        return
    loc = update.message.location
    fence = _fence(context, site)
    today = date.today().isoformat()
    try:
        if state == "awaiting_location_out":
            event = service.check_out(
                chat_id, today, latitude=loc.latitude,
                longitude=loc.longitude,
                accuracy_m=getattr(loc, "horizontal_accuracy", None),
                fence=fence, site_id=site)
            kind = "out"
        else:
            event = service.check_in(
                chat_id, today, latitude=loc.latitude,
                longitude=loc.longitude,
                accuracy_m=getattr(loc, "horizontal_accuracy", None),
                fence=fence, site_id=site)
            kind = "in"
    except DatabaseError as e:
        await update.message.reply_text(f"Could not record: {e}")
        return
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"Checked {kind} ({event.location_verdict or 'unchecked'}).",
        reply_markup=ReplyKeyboardRemove())
    await _route_to_confirmer(update, context, event)


async def _ask_site(update: Update, context: ContextTypes.DEFAULT_TYPE, resume: str) -> None:
    """Ask multi-site users to pick their active site first."""
    chat_id, _ = _me(update)
    auth = _auth(context)
    site_ids = auth.sites_for_user(chat_id)
    if not site_ids:
        await update.effective_message.reply_text("No site membership - ask an admin.")
        return
    context.user_data["pending_site_action"] = resume
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(sid, callback_data=f"att_site:{sid}"),
    ] for sid in site_ids])
    await update.effective_message.reply_text("Which site?", reply_markup=keyboard)


async def _route_to_confirmer(update, context, event) -> None:
    """Notify the site confirmer, or auto-confirm when none is configured."""
    site = _site_config(context, event.site_id)
    confirmer = site.get("confirmer") if isinstance(site, dict) else None
    if not confirmer or str(confirmer) == str(event.chat_id):
        service = _attendance(context)
        try:
            service.confirm(event.id, "system", site_id=event.site_id)
            logger.info("Auto-confirmed attendance %s (no confirmer).", event.id)
        except DatabaseError as e:
            logger.warning("Auto-confirm failed for %s: %s", event.id, e)
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Confirm", callback_data=f"att_confirm:{event.id}"),
        InlineKeyboardButton("Dispute", callback_data=f"att_dispute:{event.id}"),
    ]])
    await _notify(
        context, str(confirmer),
        f"Attendance to review: `{event.chat_id}` "
        f"({event.check_type}, {event.location_verdict or 'unchecked'}).",
        reply_markup=keyboard)


# --- callbacks ---

async def handle_attendance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id, _ = _me(update)
    service = _attendance(context)

    if data.startswith("att_site:"):
        site_id = data.split(":", 1)[1]
        auth = _auth(context)
        if site_id not in auth.sites_for_user(chat_id):
            await query.edit_message_text("Not a member of that site.")
            return
        context.user_data["active_site"] = site_id
        resume = context.user_data.pop("pending_site_action", None)
        await query.edit_message_text(f"Active site: `{site_id}`.")
        if resume == "location":
            await query.edit_message_text(
                f"Active site: `{site_id}`. Share your location again please.")
        elif resume == "assisted":
            await query.edit_message_text(
                f"Active site: `{site_id}`. Re-send your /assisted command please.")
        return

    parts = data.split(":")
    if len(parts) != 2:
        return
    action, raw_id = parts
    try:
        event_id = int(raw_id)
    except ValueError:
        return

    if action == "att_confirm":
        if not _auth(context).has_capability(chat_id, "manage_attendance"):
            await query.edit_message_text("Confirmation needs an attendance manager.")
            return
        try:
            event = service.confirm(event_id, chat_id)
        except DatabaseError as e:
            await query.edit_message_text(f"Could not confirm: {e}")
            return
        await query.edit_message_text(f"Confirmed attendance #{event.id}.")
        await _notify(context, event.chat_id,
                      f"Your check-{event.check_type} was confirmed.")
    elif action == "att_dispute":
        if not _auth(context).has_capability(chat_id, "manage_attendance"):
            await query.edit_message_text("Disputes need an attendance manager.")
            return
        context.user_data["att_dispute_id"] = event_id
        context.user_data["state"] = "awaiting_att_note"
        await query.edit_message_text("Send the dispute reason.")


async def handle_att_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    note = (update.message.text or "").strip()
    if not note:
        await update.message.reply_text("Send the dispute reason as text.")
        return
    chat_id, _ = _me(update)
    service = _attendance(context)
    try:
        event = service.dispute(context.user_data.get("att_dispute_id"),
                                chat_id, note)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not dispute: {e}")
        return
    context.user_data.pop("att_dispute_id", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Disputed attendance #{event.id}.")
    await _notify(context, event.chat_id,
                  f"Your check-{event.check_type} was disputed: {note}")


def get_registration_handlers() -> list:
    from telegram.ext import MessageHandler, filters

    return [
        CommandHandler("checkin", checkin_command),
        CommandHandler("checkout", checkout_command),
        CommandHandler("assisted", assisted_command),
        CallbackQueryHandler(handle_attendance_callback, pattern="^att_"),
        MessageHandler(filters.LOCATION, handle_location),
    ]
