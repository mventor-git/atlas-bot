"""HR request handlers: advances + transport allowances (ticket-006).

Flows (state machine in user_data):
  /advance|/transport -> amount -> reason -> [trip_date -> report_ref
  -> receipt photo | /skip] -> request created
  /hr_my, /hr_pending (buttons per role/status)
  hr_confirm / hr_delegate / hr_approve / hr_reject + month picker + notes
PDF rendering lands in 006-D; handlers stop at the approved record.
"""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot import site_session
from app.bot.keyboards import hr_menu_keyboard, hr_month_keyboard
from app.models.hr import HRRequest, HRRequestStatus, HRRequestType
from app.services.hr_service import HRService
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


def _hr(context: ContextTypes.DEFAULT_TYPE) -> HRService:
    return context.bot_data["hr_service"]


def _role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id, _ = _me(update)
    return _auth(context).get_role(chat_id)


def _req_date(req) -> str:
    return (getattr(req, "created_at", "") or "")[:10]


def _render(req: HRRequest) -> str:
    kind = "Advance" if req.request_type == HRRequestType.ADVANCE else "Transport"
    lines = [
        f"*{kind} #{req.id}* - `{req.status}`",
        f"From: {req.requester_name} (`{req.requester_chat_id}`)",
        f"Amount: {req.amount:g}",
        f"Reason: {req.reason}",
    ]
    if req.request_type == HRRequestType.TRANSPORT:
        lines.append(f"Trip: {req.trip_date or '-'} | Report: {req.report_ref or '-'}")
        if req.receipt_path:
            lines.append("Receipt: attached")
    if req.deduction_month:
        lines.append(f"Deduct from: {req.deduction_month}")
    if req.note:
        lines.append(f"Note: {req.note}")
    for sig in req.signatures or []:
        lines.append(
            f"- {sig.get('role')}/{sig.get('name')}: {sig.get('decision')}"
            + (f" ({sig.get('note')})" if sig.get("note") else "")
        )
    return "\n".join(lines)


def _action_buttons(req: HRRequest, role: str) -> InlineKeyboardMarkup:
    buttons = []
    if req.status == HRRequestStatus.PENDING and role in ("superadmin", "project_manager"):
        buttons.append([
            InlineKeyboardButton("Confirm", callback_data=f"hr_confirm:{req.id}"),
            InlineKeyboardButton("Delegate to HR", callback_data=f"hr_delegate:{req.id}"),
        ])
    if req.status == HRRequestStatus.PM_CONFIRMED and role in ("superadmin", "hr"):
        buttons.append([
            InlineKeyboardButton("Approve", callback_data=f"hr_approve:{req.id}"),
            InlineKeyboardButton("Reject", callback_data=f"hr_reject:{req.id}"),
        ])
    return InlineKeyboardMarkup(buttons) if buttons else None


# --- entry points ---

async def hr_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = _role(update, context)
    await update.effective_message.reply_text(
        "HR requests - advances and transport allowances.",
        reply_markup=hr_menu_keyboard(role),
    )


async def advance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_flow(update, context, HRRequestType.ADVANCE)


async def transport_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_flow(update, context, HRRequestType.TRANSPORT)


async def _start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    chat_id, _ = _me(update)
    send = update.effective_message.reply_text
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=f"hr:{kind}",
                                    hint=f"Press /{'advance' if kind == HRRequestType.ADVANCE else 'transport'} again.")
        return
    if not _auth(context).has_capability(chat_id, "submit_hr_request", site):
        await send("Your account is not approved yet.")
        return
    context.user_data["hr_flow"] = {"type": kind, "site": site}
    context.user_data["state"] = "awaiting_hr_amount"
    label = "advance" if kind == HRRequestType.ADVANCE else "transport allowance"
    await send(f"Amount for the {label}?")


async def my_requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, _ = _me(update)
    service = _hr(context)
    rows = []
    for site in _auth(context).sites_for_user(chat_id):   # SELF across member sites
        rows.extend(service.visible_to(chat_id, "viewer", site_id=site))
    if not rows:
        await update.effective_message.reply_text("No HR requests.")
        return
    for req in sorted(rows, key=lambda r: r.created_at)[:10]:
        markup = _action_buttons(req, _role(update, context))
        await update.effective_message.reply_text(_render(req), parse_mode="Markdown", reply_markup=markup)


async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = _role(update, context)
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="hr_pending",
                                    hint="Press the button again.")
        return
    auth = _auth(context)
    if not any(auth.has_capability(chat_id, cap, site) for cap in
               ("confirm_hr_request", "delegate_hr_request", "decide_hr_request")):
        await update.effective_message.reply_text("Approval queue needs a reviewer grant.")
        return
    rows = _hr(context).pending(site_id=site)
    if not rows:
        await update.effective_message.reply_text("Approval queue is empty.")
        return
    for req in rows[:10]:
        await update.effective_message.reply_text(
            _render(req), parse_mode="Markdown",
            reply_markup=_action_buttons(req, role),
        )


# --- text states ---

async def handle_hr_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        amount = float((update.message.text or "").strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Send a positive number for the amount.")
        return
    context.user_data["hr_flow"]["amount"] = amount
    context.user_data["state"] = "awaiting_hr_reason"
    await update.message.reply_text("Reason?")


async def handle_hr_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reason = (update.message.text or "").strip()
    if not reason:
        await update.message.reply_text("Send the reason as text.")
        return
    flow = context.user_data["hr_flow"]
    flow["reason"] = reason
    if flow["type"] == HRRequestType.TRANSPORT:
        context.user_data["state"] = "awaiting_hr_trip_date"
        await update.message.reply_text("Trip date? (YYYY-MM-DD)")
    else:
        await _create_request(update, context)


async def handle_hr_trip_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trip_date = (update.message.text or "").strip()
    if len(trip_date) != 10 or trip_date[4] != "-" or trip_date[7] != "-":
        await update.message.reply_text("Send the trip date as YYYY-MM-DD.")
        return
    context.user_data["hr_flow"]["trip_date"] = trip_date
    context.user_data["state"] = "awaiting_hr_report_ref"
    await update.message.reply_text("Linked report reference? (report date, or /hr_skip)")


async def handle_hr_report_ref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text != "/skip":
        context.user_data["hr_flow"]["report_ref"] = text
    context.user_data["state"] = "awaiting_hr_receipt"
    await update.message.reply_text("Send the receipt photo, or /hr_skip.")


async def handle_hr_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") != "awaiting_hr_receipt":
        return  # not our flow - ignore silently
    flow = context.user_data.get("hr_flow", {})
    if not update.message.photo:
        await update.message.reply_text("Send a photo of the receipt, or /skip.")
        return
    try:
        photo = update.message.photo[-1]
        target = Path("exports/receipts")
        target.mkdir(parents=True, exist_ok=True)
        dest = target / f"pending_{flow.get('type', 'doc')}_{update.effective_user.id}.jpg"
        tg_file = await photo.get_file()
        await tg_file.download_to_drive(str(dest))
        flow["receipt_path"] = str(dest)
    except Exception as e:
        logger.warning("Receipt download failed: %s", e)
        await update.message.reply_text("Could not save that photo - try again or /skip.")
        return
    await _create_request(update, context)


async def handle_hr_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")
    flow = context.user_data.get("hr_flow", {})
    if state == "awaiting_hr_report_ref":
        context.user_data["state"] = "awaiting_hr_receipt"
        await update.message.reply_text("Send the receipt photo, or /hr_skip.")
    elif state == "awaiting_hr_receipt":
        await _create_request(update, context)
    elif state == "awaiting_lmo_reason" and flow.get("type") == "overtime":
        await handle_lmo_skip(update, context)
    else:
        await update.message.reply_text("Nothing to skip.")


async def _create_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, name = _me(update)
    flow = context.user_data.get("hr_flow", {})
    service = _hr(context)
    try:
        if flow.get("type") == HRRequestType.TRANSPORT:
            req = service.request_transport(
                chat_id, name, flow["amount"], flow["reason"],
                trip_date=flow.get("trip_date", ""),
                report_ref=flow.get("report_ref", ""),
                receipt_path=flow.get("receipt_path", ""),
                site_id=flow.get("site"),
            )
        else:
            req = service.request_advance(
                chat_id, name, flow["amount"], flow["reason"],
                site_id=flow.get("site"))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not file the request: {e}")
        return
    context.user_data.pop("hr_flow", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"Filed:\n{_render(req)}\n\nStatus: pending PM confirmation.",
        parse_mode="Markdown",
    )


async def handle_reject_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    note = (update.message.text or "").strip()
    req_id = context.user_data.get("hr_reject_id")
    chat_id, name = _me(update)
    service = _hr(context)
    try:
        req = service.decide_hr(req_id, chat_id, name, False, note=note,
                                site_id=context.user_data.get("hr_reject_site"))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not reject: {e}")
        return
    context.user_data.pop("hr_reject_id", None)
    context.user_data.pop("hr_reject_site", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Rejected:\n{_render(req)}", parse_mode="Markdown")
    from app.bot.notify import notify

    await notify(context, "leave_decision", req.requester_chat_id,
                 req.site_id, reference=f"hr:{req.id}", date=_req_date(req),
                 kind=req.request_type, verdict="rejected", note=note)


async def handle_delegate_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = (update.message.text or "").strip()
    req_id = context.user_data.get("hr_delegate_id")
    site = context.user_data.get("hr_delegate_site")
    chat_id, name = _me(update)
    auth = _auth(context)
    service = _hr(context)
    req = service.get(req_id, site_id=site)
    if req is None:
        await update.message.reply_text("Request not found.")
        return
    # Target must be able to decide HR requests AT THE REQUEST'S SITE
    # (memberships are the authority; "hr" is only a label).
    if not auth.has_capability(target, "decide_hr_request", req.site_id):
        await update.message.reply_text(
            "Target has no HR decision rights at this site.")
        return
    try:
        req = service.delegate_to_hr(req_id, chat_id, name, target, site_id=site)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not delegate: {e}")
        return
    context.user_data.pop("hr_delegate_id", None)
    context.user_data.pop("hr_delegate_site", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Delegated to HR:\n{_render(req)}", parse_mode="Markdown")
    from app.bot.notify import notify

    await notify(context, "case_update", target, req.site_id,
                 reference=f"hr:{req.id}", date=_req_date(req),
                 kind=req.request_type, ref=req.id,
                 verdict="delegated to HQ", note="")


async def handle_deduction_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import re

    month = (update.message.text or "").strip()
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month):
        await update.message.reply_text("Send the deduction month as YYYY-MM.")
        return
    req_id = context.user_data.get("hr_approve_id")
    chat_id, name = _me(update)
    service = _hr(context)
    try:
        req = service.decide_hr(req_id, chat_id, name, True, deduction_month=month,
                                site_id=context.user_data.get("hr_approve_site"))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not approve: {e}")
        return
    context.user_data.pop("hr_approve_id", None)
    context.user_data.pop("hr_approve_site", None)
    context.user_data.pop("state", None)
    await _after_approval(update, context, service, req)
    await update.message.reply_text(f"Approved:\n{_render(req)}", parse_mode="Markdown")
    from app.bot.notify import notify

    await notify(context, "leave_decision", req.requester_chat_id,
                 req.site_id, reference=f"hr:{req.id}", date=_req_date(req),
                 kind=req.request_type, verdict="approved",
                 note=f"Deduct from: {month}")


async def _after_approval(update, context, service, req) -> None:
    """Render + record + queue the approved PDF (006-D). Never fails the approval."""
    try:
        from app.libre.hr_fill import queue_for_print, render_pdf

        config = context.bot_data["app_config"]
        pdf = render_pdf(req, config)
        service.record_pdf(req.id, pdf, site_id=req.site_id)
        queue_for_print(pdf)
        logger.info("HR request #%s rendered + queued: %s", req.id, pdf)
    except Exception as e:
        logger.warning("HR PDF render/queue failed for #%s: %s",
                       getattr(req, "id", "?"), e)


# --- callbacks ---

async def handle_hr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id, name = _me(update)
    role = _role(update, context)

    if data == "hr_new_advance":
        await _start_flow(update, context, HRRequestType.ADVANCE)
        return
    if data == "hr_new_transport":
        await _start_flow(update, context, HRRequestType.TRANSPORT)
        return
    if data == "hr_my":
        context.user_data.pop("state", None)
        await my_requests_command(update, context)
        return
    if data == "hr_pending":
        await pending_command(update, context)
        return

    parts = data.split(":")
    if parts[0] in ("hr_sites", "hr_site", "hr_print", "hr_notify"):
        if await handle_board_callback(update, context, parts[0], parts):
            return

    if len(parts) < 2:
        return
    service = _hr(context)
    action = parts[0]
    try:
        req_id = int(parts[-1])
    except ValueError:
        return

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=data,
                                    hint="Press the button again.")
        return

    if action == "hr_confirm":
        if not _auth(context).has_capability(chat_id, "confirm_hr_request", site):
            await query.edit_message_text("PM confirmation needs a PM account.")
            return
        try:
            req = service.confirm_pm(req_id, chat_id, name, site_id=site)
        except DatabaseError as e:
            await query.edit_message_text(f"Could not confirm: {e}")
            return
        await query.edit_message_text(f"Confirmed:\n{_render(req)}", parse_mode="Markdown")
        from app.bot.notify import notify

        await notify(context, "leave_decision", req.requester_chat_id,
                     req.site_id, reference=f"hr:{req.id}", date=_req_date(req),
                     kind=req.request_type, verdict="PM-confirmed, sent to HR",
                     note="")
    elif action == "hr_delegate":
        if not _auth(context).has_capability(chat_id, "delegate_hr_request", site):
            await query.edit_message_text("Delegation needs a PM account.")
            return
        context.user_data["hr_delegate_id"] = req_id
        context.user_data["hr_delegate_site"] = site
        context.user_data["state"] = "awaiting_delegate_target"
        await query.edit_message_text("Send the HQ HR chat ID to delegate to.")
    elif action == "hr_approve":
        if not _auth(context).has_capability(chat_id, "decide_hr_request", site):
            await query.edit_message_text("Approval needs an HR account.")
            return
        req = service.get(req_id, site_id=site)
        if req is None:
            await query.edit_message_text("Request not found.")
            return
        if req.request_type == HRRequestType.ADVANCE:
            context.user_data["hr_approve_id"] = req_id
            context.user_data["hr_approve_site"] = site
            context.user_data["state"] = "awaiting_deduction_month"
            await query.edit_message_text(
                f"Approve advance #{req_id} - pick the deduction month:",
                reply_markup=hr_month_keyboard(req_id),
            )
        else:
            try:
                decided = service.decide_hr(req_id, chat_id, name, True,
                                            site_id=site)
            except DatabaseError as e:
                await query.edit_message_text(f"Could not approve: {e}")
                return
            await _after_approval(update, context, service, decided)
            await query.edit_message_text(f"Approved:\n{_render(decided)}", parse_mode="Markdown")
            from app.bot.notify import notify

            await notify(context, "leave_decision", decided.requester_chat_id,
                         decided.site_id, reference=f"hr:{decided.id}",
                         date=_req_date(decided), kind=decided.request_type,
                         verdict="approved", note="")
    elif action == "hr_reject":
        if not _auth(context).has_capability(chat_id, "decide_hr_request", site):
            await query.edit_message_text("Rejection needs an HR account.")
            return
        context.user_data["hr_reject_id"] = req_id
        context.user_data["hr_reject_site"] = site
        context.user_data["state"] = "awaiting_reject_note"
        await query.edit_message_text("Send the rejection note.")
    elif action == "hr_month":
        if not _auth(context).has_capability(chat_id, "decide_hr_request", site):
            await query.edit_message_text("Approval needs an HR account.")
            return
        month = parts[1]
        try:
            req = service.decide_hr(req_id, chat_id, name, True,
                                    deduction_month=month, site_id=site)
        except DatabaseError as e:
            await query.edit_message_text(f"Could not approve: {e}")
            return
        context.user_data.pop("hr_approve_id", None)
        context.user_data.pop("state", None)
        await _after_approval(update, context, service, req)
        await query.edit_message_text(f"Approved:\n{_render(req)}", parse_mode="Markdown")
        from app.bot.notify import notify

        await notify(context, "leave_decision", req.requester_chat_id,
                     req.site_id, reference=f"hr:{req.id}", date=_req_date(req),
                     kind=req.request_type, verdict="approved",
                     note=f"Deduct from: {month}")


def get_registration_handlers() -> list:
    from telegram.ext import MessageHandler, filters

    return [
        CommandHandler("hr", hr_menu_command),
        CommandHandler("advance", advance_command),
        CommandHandler("transport", transport_command),
        CommandHandler("hr_my", my_requests_command),
        CommandHandler("hr_pending", pending_command),
        CommandHandler("hr_skip", handle_hr_skip),
        CommandHandler("hr_sites", sites_command),
        CommandHandler("hr_print_all", print_all_command),
        CommandHandler("hr_member", member_command),
        CommandHandler("hr_pay", pay_command),
        CommandHandler("hr_deduct", deduct_command),
        CommandHandler("leave", leave_command),
        CommandHandler("mission", mission_command),
        CommandHandler("overtime", overtime_command),
        CallbackQueryHandler(handle_hr_callback, pattern="^hr_"),
        MessageHandler(filters.PHOTO, handle_hr_receipt),
    ]


# --- HR site board (006-E) ---

def _require_hr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    # Capability is the authority (view_hq_reports ships in the hr bundle);
    # the old ("superadmin", "hr") role string check bypassed tenancy.
    chat_id, _ = _me(update)
    return _auth(context).has_capability(chat_id, "view_hq_reports")


def _sites(config) -> list:
    raw = getattr(config, "sites", None) or []
    return [{"id": str(s.get("id")), "name": s.get("name", s.get("id"))}
            for s in raw if isinstance(s, dict) and s.get("id")]


async def sites_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """HR site board entry: pick a site."""
    if not _require_hr(update, context):
        await update.effective_message.reply_text("Site board is for HR only.")
        return
    sites = _sites(context.bot_data["app_config"])
    if not sites:
        await update.effective_message.reply_text("No sites configured.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(s["name"], callback_data=f"hr_site:{s['id']}")]
        for s in sites
    ])
    await update.effective_message.reply_text("Pick a site:", reply_markup=keyboard)


async def print_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Queue today's reports for every site that has one."""
    if not _require_hr(update, context):
        await update.effective_message.reply_text("Print-all is for HR only.")
        return
    from datetime import date as _date

    today = _date.today().isoformat()
    sites = _sites(context.bot_data["app_config"])
    printed, missing = [], []
    for site in sites:
        pdf = _ensure_report_pdf(context, today, site["id"])
        if pdf:
            printed.append(site["name"])
        else:
            missing.append(site["name"])
    await update.effective_message.reply_text(
        "Printed: %s\nMissing: %s" % (", ".join(printed) or "-", ", ".join(missing) or "-")
    )


async def member_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Membership administration (Phase 1; the source of truth):

    /hr_member grant  <chat_id> <site_id> [caps|comma,list|role]
    /hr_member suspend <chat_id> <site_id>
    /hr_member revoke  <chat_id> <site_id>

    Superadmin only (bootstrap authority). Sites are validated against
    config.sites so typos cannot mint orphan permissions.
    """
    chat_id, _ = _me(update)
    if not _auth(context).is_super_admin(chat_id):
        await update.message.reply_text("Only the superadmin manages memberships.")
        return
    parts = (update.message.text or "").split()
    if len(parts) < 4:
        await update.message.reply_text(
            "Usage: /hr_member grant|suspend|revoke <chat_id> <site_id> [caps]")
        return
    _, action, target, site_id = parts[:4]
    known = {s["id"] for s in _sites(context.bot_data.get("app_config"))}
    if site_id not in known:
        await update.message.reply_text(
            f"Unknown site `{site_id}`. Configured: {', '.join(sorted(known))}")
        return
    auth = _auth(context)
    try:
        if action == "grant":
            caps = parts[4] if len(parts) > 4 else ""
            caps_list = [] if not caps or caps.lower() == "role" else \
                [c.strip() for c in caps.split(",") if c.strip()]
            before = auth.find_membership(target, site_id) is not None
            auth.grant_membership(target, site_id, caps_list)
            await update.message.reply_text(
                f"Membership {'updated' if before else 'granted'}: `{target}` @ "
                f"`{site_id}` ({'role defaults' if not caps_list else str(len(caps_list)) + ' explicit caps'}).")
        elif action == "suspend":
            ok = auth.suspend_membership(target, site_id)
            await update.message.reply_text(
                f"Suspended `{target}` @ `{site_id}`." if ok else "No such membership.")
        elif action == "revoke":
            ok = auth.revoke_membership(target, site_id)
            await update.message.reply_text(
                f"Revoked `{target}` @ `{site_id}`." if ok else "No such membership.")
        else:
            await update.message.reply_text("Action must be grant|suspend|revoke.")
    except ValueError as e:
        await update.message.reply_text(f"Rejected: {e}")


async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm a payout: /hr_pay <request_id> <amount> <YYYY-MM-DD> [reference]."""
    chat_id, name = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="hr_pay",
                                    hint="Re-send /hr_pay ...")
        return
    if not _auth(context).has_capability(chat_id, "confirm_payout", site):
        await update.message.reply_text("Payout confirmation needs a finance/HR grant.")
        return
    parts = (update.message.text or "").split(maxsplit=4)
    if len(parts) < 4:
        await update.message.reply_text("Usage: /hr_pay <request_id> <amount> <YYYY-MM-DD> [reference]")
        return
    _, req_id, amount, payout_date = parts[:4]
    reference = parts[4] if len(parts) > 4 else ""
    service = _hr(context)
    try:
        event = service.record_payout(int(req_id), float(amount), payout_date,
                                      chat_id, reference=reference, site_id=site)
    except (DatabaseError, ValueError) as e:
        await update.message.reply_text(f"Could not record payout: {e}")
        return
    status = service.financial_status(event.request_id, site_id=site)
    await update.message.reply_text(
        f"Payout recorded: {event.amount:g} on {event.payout_date} "
        f"(total paid {status['paid']:g}). State: {status['state']}.")
    req = service.get(event.request_id, site_id=site)
    if req is not None:
        from app.bot.notify import notify

        await notify(context, "case_update", req.requester_chat_id, site,
                     reference=f"hr:{req.id}", date=_req_date(req),
                     kind=f"{req.request_type} payout", ref=req.id,
                     verdict=f"{event.amount:g} paid on {event.payout_date}",
                     note=reference)


async def deduct_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm a payroll deduction: /hr_deduct <request_id> <amount> <YYYY-MM> [reference]."""
    chat_id, name = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="hr_deduct",
                                    hint="Re-send /hr_deduct ...")
        return
    if not _auth(context).has_capability(chat_id, "confirm_payroll_deduction", site):
        await update.message.reply_text("Deduction confirmation needs a payroll grant.")
        return
    parts = (update.message.text or "").split(maxsplit=4)
    if len(parts) < 4:
        await update.message.reply_text("Usage: /hr_deduct <request_id> <amount> <YYYY-MM> [reference]")
        return
    _, req_id, amount, period = parts[:4]
    reference = parts[4] if len(parts) > 4 else ""
    service = _hr(context)
    try:
        event = service.record_deduction(int(req_id), float(amount), period,
                                         chat_id, reference=reference, site_id=site)
    except (DatabaseError, ValueError) as e:
        await update.message.reply_text(f"Could not record deduction: {e}")
        return
    status = service.financial_status(event.request_id, site_id=site)
    await update.message.reply_text(
        f"Deduction recorded: {event.amount:g} for {event.period} "
        f"(total deducted {status['deducted']:g}). State: {status['deduction_state']}.")
    req = service.get(event.request_id, site_id=site)
    if req is not None:
        from app.bot.notify import notify

        await notify(context, "case_update", req.requester_chat_id, site,
                     reference=f"hr:{req.id}", date=_req_date(req),
                     kind=f"{req.request_type} deduction", ref=req.id,
                     verdict=f"{event.amount:g} deducted ({event.period})",
                     note=reference)


# --- Leave / mission / overtime conversational flows (011) ---

async def _lmo_start(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     kind: str, cap: str, first_state: str, prompt: str) -> None:
    """Gate + session-site capture shared by /leave /mission /overtime."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=f"lmo:{kind}",
                                    hint=f"Press /{kind} again.")
        return
    if not _auth(context).has_capability(chat_id, cap, site):
        await update.message.reply_text("That request type needs an approved account.")
        return
    context.user_data["hr_flow"] = {"type": kind, "site": site}
    context.user_data["state"] = first_state
    await update.message.reply_text(prompt)


async def leave_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a leave request: reason -> start -> end."""
    await _lmo_start(update, context, "leave", "submit_leave",
                     "awaiting_lmo_reason", "Leave reason?")


async def mission_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a mission request: purpose -> start -> end."""
    await _lmo_start(update, context, "mission", "submit_mission",
                     "awaiting_lmo_reason", "Mission purpose?")


async def overtime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start an overtime request: date -> hours -> reason."""
    await _lmo_start(update, context, "overtime", "request_overtime",
                     "awaiting_lmo_date", "Overtime date? (YYYY-MM-DD)")


async def handle_lmo_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reason = (update.message.text or "").strip()
    if not reason:
        await update.message.reply_text("Send the reason as text.")
        return
    flow = context.user_data["hr_flow"]
    flow["reason"] = reason
    if flow["type"] == "overtime":
        await _create_lmo_request(update, context)
    else:
        context.user_data["state"] = "awaiting_lmo_start"
        await update.message.reply_text("Start date? (YYYY-MM-DD)")


async def handle_lmo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        await update.message.reply_text("Send the start date as YYYY-MM-DD.")
        return
    context.user_data["hr_flow"]["start"] = text
    context.user_data["state"] = "awaiting_lmo_end"
    await update.message.reply_text("End date? (YYYY-MM-DD, same as start for one day)")


async def handle_lmo_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        await update.message.reply_text("Send the end date as YYYY-MM-DD.")
        return
    context.user_data["hr_flow"]["end"] = text
    await _create_lmo_request(update, context)


async def handle_lmo_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        await update.message.reply_text("Send the date as YYYY-MM-DD.")
        return
    context.user_data["hr_flow"]["date"] = text
    context.user_data["state"] = "awaiting_lmo_hours"
    await update.message.reply_text("How many hours? (max 24)")


async def handle_lmo_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hours = float((update.message.text or "").strip())
        if hours <= 0 or hours > 24:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Send hours as a number within (0, 24].")
        return
    context.user_data["hr_flow"]["hours"] = hours
    context.user_data["state"] = "awaiting_lmo_reason"
    await update.message.reply_text("Reason? (or /hr_skip)")


async def handle_lmo_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.user_data.get("hr_flow", {})
    if flow.get("type") == "overtime" and context.user_data.get("state") == "awaiting_lmo_reason":
        flow["reason"] = "overtime"
        await _create_lmo_request(update, context)
    else:
        await update.message.reply_text("Nothing to skip.")


async def _create_lmo_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, name = _me(update)
    flow = context.user_data.get("hr_flow", {})
    service = _hr(context)
    kind = flow.get("type")
    try:
        if kind == "leave":
            req = service.request_leave(
                chat_id, name, flow["reason"], flow["start"], flow["end"],
                site_id=flow.get("site"))
        elif kind == "mission":
            req = service.request_mission(
                chat_id, name, flow["reason"], flow["start"], flow["end"],
                site_id=flow.get("site"))
        elif kind == "overtime":
            req = service.request_overtime(
                chat_id, name, flow.get("date", ""), flow["hours"],
                flow.get("reason", "overtime"), site_id=flow.get("site"))
        else:
            await update.message.reply_text("Unknown request type - start over.")
            return
    except DatabaseError as e:
        await update.message.reply_text(f"Could not file the request: {e}")
        return
    context.user_data.pop("hr_flow", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"Filed {kind} #{req.id}:\n{_render(req)}\n\nStatus: pending PM confirmation.",
        parse_mode="Markdown",
    )


def _ensure_report_pdf(context: ContextTypes.DEFAULT_TYPE, date_str: str, site_id: str):
    """Return a queued PDF path for a site report, generating if needed."""
    from app.libre.filler import TemplateFiller
    from app.libre.hr_fill import queue_for_print
    from app.libre.pdf import PDFGenerator

    config = context.bot_data["app_config"]
    repo = context.bot_data["report_repository"]
    report = repo.get_by_date(date_str, site_id=site_id)
    if report is None:
        return None
    if report.pdf_path and Path(report.pdf_path).exists():
        return queue_for_print(report.pdf_path)
    filled = TemplateFiller(config).fill(report)
    pdf = PDFGenerator(config).convert_to_pdf(filled)
    report.pdf_path = pdf
    try:
        repo.update(report, force=True)
    except Exception:
        pass
    return queue_for_print(pdf)


async def handle_board_callback(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                action: str, parts: list) -> bool:
    """Site-board callbacks. Returns True if handled."""
    from datetime import date as _date

    query = update.callback_query

    def bad_site(site_id: str) -> bool:
        # Client-supplied site strings are never trusted: config whitelist
        # membership is mandatory for every board/print/notify action.
        return site_id not in {s["id"] for s in _sites(
            context.bot_data.get("app_config"))}

    if action == "hr_sites":
        await sites_command(update, context)
        return True
    if action == "hr_site":
        if not _require_hr(update, context):
            await query.edit_message_text("Site board is for HR only.")
            return True
        site_id = parts[1]
        if bad_site(site_id):
            await query.edit_message_text("Unknown site.")
            return True
        today = _date.today().isoformat()
        repo = context.bot_data["report_repository"]
        report = repo.get_by_date(today, site_id=site_id)
        if report is None:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Notify site", callback_data=f"hr_notify:{site_id}"),
            ]])
            await query.edit_message_text(
                f"Site `{site_id}` - no report for {today}.", reply_markup=keyboard)
        else:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Print", callback_data=f"hr_print:{today}:{site_id}"),
            ]])
            await query.edit_message_text(
                f"Site `{site_id}` - report `{report.status.value}` "
                f"({len(report.items or [])} items).",
                reply_markup=keyboard, parse_mode="Markdown")
        return True
    if action == "hr_print":
        if not _require_hr(update, context):
            await query.edit_message_text("Printing is for HR only.")
            return True
        _, date_str, site_id = parts[0], parts[1], parts[2]
        if bad_site(site_id):
            await query.edit_message_text("Unknown site.")
            return True
        try:
            queued = _ensure_report_pdf(context, date_str, site_id)
        except Exception as e:
            logger.warning("Board print failed: %s", e)
            queued = None
        await query.edit_message_text(
            f"Queued for print: `{queued}`" if queued else "Nothing to print.")
        return True
    if action == "hr_notify":
        if not _require_hr(update, context):
            await query.edit_message_text("Notify is for HR only.")
            return True
        site_id = parts[1]
        if bad_site(site_id):
            await query.edit_message_text("Unknown site.")
            return True
        # Memberships (not legacy users.site_id) decide who belongs here.
        # Durable outbox: deduped, retried, restart-safe (031).
        from datetime import date as _date

        from app.bot.notify import notify

        targets = _auth(context).chat_ids_for_site(site_id)
        today = _date.today().isoformat()
        queued = 0
        for chat in targets:
            await notify(context, "report_missing", chat, site_id,
                         reference=f"missing:{today}", date=today)
            queued += 1
        await query.edit_message_text(
            f"Queued {queued} reminder(s) for `{site_id}` members.")
        return True
    return False
