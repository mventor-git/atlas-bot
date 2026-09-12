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


def _days(context: ContextTypes.DEFAULT_TYPE):
    """Day-aggregate service (026; may be absent in legacy tests)."""
    return context.bot_data.get("attendance_day_service")


async def _day_hook(context: ContextTypes.DEFAULT_TYPE, event) -> None:
    """Fold a fresh event into its day; day must never break attendance."""
    service = _days(context)
    if service is None:
        return
    try:
        service.on_event(event)
    except DatabaseError as e:
        logger.warning("Day hook failed for event %s: %s",
                       getattr(event, "id", "?"), e)


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
    await _day_hook(context, event)
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
    await _day_hook(context, event)
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
            event = service.confirm(event.id, "system", site_id=event.site_id)
            logger.info("Auto-confirmed attendance %s (no confirmer).", event.id)
            await _day_hook(context, event)
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
            await _day_hook(context, event)
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


# --- day aggregate (026) ---

def _render_day(view: dict) -> str:
    day = view["day"]
    lines = [
        f"*Day {day.day_date}* at `{day.site_id}` - `{day.status}`",
        f"Origin: {day.origin}",
    ]
    if day.first_in:
        lines.append(f"First in: {day.first_in[11:16]}")
    if day.last_out:
        lines.append(f"Last out: {day.last_out[11:16]}")
    if day.late_minutes:
        lines.append(f"Late: {day.late_minutes} min (fact, not punishment)")
    if day.verdict:
        lines.append(f"Verdict: `{day.verdict}`")
    if day.resolution_note:
        lines.append(f"Resolution: {day.resolution_note}")
    anomalies = view["anomalies"]
    if anomalies:
        lines.append("Anomalies: " + ", ".join(anomalies))
    if view.get("required") is False:
        lines.append(f"Non-working day ({view.get('calendar_note') or 'calendar'})")
    claims = [c for c in view["claims"] if c.status == "open"]
    if claims:
        lines.append(f"Open claims: {len(claims)}")
    n_events = len(view["events"])
    lines.append(f"Evidence entries: {n_events}")
    return "\n".join(lines)


async def myday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show my day aggregate: /myday [YYYY-MM-DD]."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="myday",
                                    hint="Press /myday again.")
        return
    if not _auth(context).has_capability(chat_id, "view_own_attendance", site):
        await update.message.reply_text("Attendance view needs an approved account.")
        return
    parts = (update.message.text or "").split()
    day_date = parts[1] if len(parts) > 1 else date.today().isoformat()
    view = _days(context).day_view(chat_id, day_date, site_id=site)
    markup = None
    day = view["day"]
    if day.status in ("confirmed", "resolved"):
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("Dispute day",
                                 callback_data=f"day_dispute:{day.id}")]])
    await update.message.reply_text(
        _render_day(view), parse_mode="Markdown", reply_markup=markup)


async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/attendance_queue [YYYY-MM-DD]: days needing action (sweep + list)."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="queue",
                                    hint="Press /attendance_queue again.")
        return
    auth = _auth(context)
    if not auth.has_capability(chat_id, "manage_attendance", site):
        await update.message.reply_text("Needs an attendance manager.")
        return
    parts = (update.message.text or "").split()
    day_date = parts[1] if len(parts) > 1 else date.today().isoformat()
    service = _days(context)
    roster = auth.chat_ids_for_site(site, "check_in")
    service.sweep(roster, day_date, site_id=site)
    rows = service.queue(day_date, site_id=site)
    if not rows:
        await update.message.reply_text(f"No days needing action for {day_date}.")
        return
    for day in rows[:10]:
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("Resolve",
                                 callback_data=f"day_resolve:{day.id}")]])
        await update.message.reply_text(
            _render_day({"day": day, "events": [], "anomalies": [],
                         "claims": []}),
            parse_mode="Markdown", reply_markup=markup)


async def daynote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/daynote <chat_id> <YYYY-MM-DD> <text...>: manager finding."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="daynote",
                                    hint="Re-send /daynote ...")
        return
    if not _auth(context).has_capability(chat_id, "manage_attendance", site):
        await update.message.reply_text("Needs an attendance manager.")
        return
    parts = (update.message.text or "").split(maxsplit=3)
    if len(parts) < 4:
        await update.message.reply_text("Usage: /daynote <chat_id> <date> <text>")
        return
    _, target, day_date, text = parts
    try:
        claim = _days(context).claim(target, day_date, "note", text,
                                     raised_by=chat_id, site_id=site)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not record: {e}")
        return
    await update.message.reply_text(f"Finding recorded on {target}/{day_date}.")


async def dayclaim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/day_claim <YYYY-MM-DD> <text...>: correction claim on my own day."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="claim",
                                    hint="Re-send /day_claim ...")
        return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: /day_claim <date> <text>")
        return
    _, day_date, text = parts
    try:
        claim = _days(context).claim(chat_id, day_date, "correction", text,
                                     raised_by=chat_id, site_id=site)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not file: {e}")
        return
    await update.message.reply_text(f"Claim `#{claim.id}` filed for review.")


async def claims_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/day_claims: open correction/note claims needing a decision."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="claims",
                                    hint="Press /day_claims again.")
        return
    if not _auth(context).has_capability(chat_id, "manage_attendance", site):
        await update.message.reply_text("Needs an attendance manager.")
        return
    rows = _days(context).open_claims(site_id=site)
    if not rows:
        await update.message.reply_text("No open claims.")
        return
    for claim in rows[:10]:
        await update.message.reply_text(
            f"*Claim #{claim.id}* ({claim.kind}) `{claim.chat_id}` "
            f"{claim.day_date}\n{claim.text}", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Approve",
                                     callback_data=f"day_claim_ok:{claim.id}"),
                InlineKeyboardButton("Deny",
                                     callback_data=f"day_claim_no:{claim.id}"),
            ]]))


async def handle_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id, _ = _me(update)

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=data,
                                    hint="Press the button again.")
        return

    parts = data.split(":")
    if len(parts) != 2:
        return
    action, raw_id = parts
    try:
        day_id = int(raw_id)
    except ValueError:
        return

    if action == "day_dispute":
        day = _days(context).get_day(day_id, site_id=site)
        if day is None or str(day.chat_id) != chat_id:
            await query.edit_message_text("Only the employee may dispute this day.")
            return
        context.user_data["day_dispute_id"] = day_id
        context.user_data["state"] = "awaiting_day_note"
        await query.edit_message_text(
            f"Day `#{day.id}` - send your dispute note.")
    elif action == "day_resolve":
        if not _auth(context).has_capability(chat_id, "manage_attendance", site):
            await query.edit_message_text("Needs an attendance manager.")
            return
        day = _days(context).get_day(day_id, site_id=site)
        if day is None:
            await query.edit_message_text("Day not found in this site.")
            return
        context.user_data["day_resolve_id"] = day_id
        context.user_data["state"] = "awaiting_day_resolve"
        await query.edit_message_text(
            "Send resolution as: `<verdict> | <note>`\n"
            "Verdicts: present, present_late, absent, excused, "
            "on_mission, authorized_outside.")
    elif action in ("day_claim_ok", "day_claim_no"):
        if not _auth(context).has_capability(chat_id, "manage_attendance", site):
            await query.edit_message_text("Needs an attendance manager.")
            return
        context.user_data["day_claim_id"] = day_id
        context.user_data["day_claim_approve"] = action == "day_claim_ok"
        context.user_data["state"] = "awaiting_claim_note"
        await query.edit_message_text("Send the decision note.")


async def handle_day_dispute_note(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
    note = (update.message.text or "").strip()
    if not note:
        await update.message.reply_text("Send your dispute note as text.")
        return
    chat_id, _ = _me(update)
    try:
        day = _days(context).dispute(
            context.user_data.get("day_dispute_id"), chat_id, note,
            site_id=site_session.resolve_site(update, context))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not dispute: {e}")
        return
    context.user_data.pop("day_dispute_id", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Day `#{day.id}` disputed - pending re-review.")


async def handle_day_resolve(update: Update,
                             context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if "|" not in text:
        await update.message.reply_text(
            "Send resolution as: `<verdict> | <note>` (or /cancel).")
        return
    verdict, note = (part.strip() for part in text.split("|", 1))
    chat_id, _ = _me(update)
    try:
        day = _days(context).resolve(
            context.user_data.get("day_resolve_id"), chat_id, verdict, note,
            site_id=site_session.resolve_site(update, context))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not resolve: {e}")
        return
    context.user_data.pop("day_resolve_id", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Day `#{day.id}` resolved: `{day.verdict}`.")
    await _notify(context, day.chat_id,
                  f"Your attendance for `{day.day_date}` was resolved: {day.verdict}.")


async def handle_claim_note(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> None:
    note = (update.message.text or "").strip()
    if not note:
        await update.message.reply_text("Send the decision note as text.")
        return
    chat_id, _ = _me(update)
    try:
        claim = _days(context).decide_claim(
            context.user_data.get("day_claim_id"), chat_id,
            context.user_data.get("day_claim_approve", False), note,
            site_id=site_session.resolve_site(update, context))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not decide: {e}")
        return
    context.user_data.pop("day_claim_id", None)
    context.user_data.pop("day_claim_approve", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Claim `#{claim.id}` {claim.status}.")


def get_registration_handlers() -> list:
    from telegram.ext import MessageHandler, filters

    return [
        CommandHandler("checkin", checkin_command),
        CommandHandler("checkout", checkout_command),
        CommandHandler("assisted", assisted_command),
        CommandHandler("myday", myday_command),
        CommandHandler("attendance_queue", queue_command),
        CommandHandler("daynote", daynote_command),
        CommandHandler("day_claim", dayclaim_command),
        CommandHandler("day_claims", claims_command),
        CallbackQueryHandler(handle_attendance_callback, pattern="^att_"),
        CallbackQueryHandler(handle_day_callback, pattern="^day_"),
        MessageHandler(filters.LOCATION, handle_location),
    ]
