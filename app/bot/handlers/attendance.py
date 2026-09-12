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

from app.bot import site_session
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
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="checkin",
                                    hint="Press /checkin again.")
        return
    if not _auth(context).has_capability(chat_id, "check_in", site):
        await update.message.reply_text("Check-in needs an approved account.")
        return
    context.user_data["state"] = "awaiting_location_in"
    await update.message.reply_text(
        f"Share your live location to check in at `{site}` (or /cancel).",
        reply_markup=_location_keyboard(), parse_mode="Markdown")


async def checkout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start check-out: ask for a live location share."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="checkout",
                                    hint="Press /checkout again.")
        return
    if not _auth(context).has_capability(chat_id, "check_out", site):
        await update.message.reply_text("Check-out needs an approved account.")
        return
    context.user_data["state"] = "awaiting_location_out"
    await update.message.reply_text(
        f"Share your live location to check out at `{site}` (or /cancel).",
        reply_markup=_location_keyboard(), parse_mode="Markdown")


async def assisted_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Proxy check-in: /assisted <target_chat_id> <reason...>."""
    chat_id, name = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="assisted",
                                    hint="Re-send your /assisted command.")
        return
    if not _auth(context).has_capability(chat_id, "check_in", site):
        await update.message.reply_text("Assisted check-in needs an approved account.")
        return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: /assisted <target_chat_id> <reason>")
        return
    _, target, reason = parts
    service = _attendance(context)
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
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(
            update, context, resume=state,
            hint="Share your location again.")
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


async def _route_to_confirmer(update, context, event) -> None:
    """Notify the first eligible confirmer, else system auto-confirm.

    Chain: primary confirmer -> confirmer_fallback site key -> auto-confirm.
    Nobody confirms their own check-in. Time-based escalation is OUT
    (needs a scheduler + owner timing values).
    """
    site = _site_config(context, event.site_id)
    candidates = []
    if isinstance(site, dict):
        candidates = [site.get("confirmer"), site.get("confirmer_fallback")]
    target = next(
        (c for c in candidates if c and str(c) != str(event.chat_id)), None)
    if target is None:
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
        context, str(target),
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

    parts = data.split(":")
    if len(parts) != 2:
        return
    action, raw_id = parts
    try:
        event_id = int(raw_id)
    except ValueError:
        return

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=data,
                                    hint="Press the button again.")
        return
    if not _auth(context).has_capability(chat_id, "manage_attendance", site):
        await query.edit_message_text(
            f"Needs an attendance manager at your active site (`{site}`); "
            "switch with /site if needed.")
        return

    if action == "att_confirm":
        try:
            event = service.confirm(event_id, chat_id, site_id=site)
        except DatabaseError as e:
            await query.edit_message_text(f"Could not confirm: {e}")
            return
        await query.edit_message_text(f"Confirmed attendance #{event.id}.")
        await _notify(context, event.chat_id,
                      f"Your check-{event.check_type} was confirmed.")
    elif action == "att_dispute":
        context.user_data["att_dispute_id"] = event_id
        context.user_data["att_dispute_event_site"] = site
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
        event = service.dispute(
            context.user_data.get("att_dispute_id"), chat_id, note,
            site_id=context.user_data.get("att_dispute_event_site"))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not dispute: {e}")
        return
    context.user_data.pop("att_dispute_id", None)
    context.user_data.pop("att_dispute_event_site", None)
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
