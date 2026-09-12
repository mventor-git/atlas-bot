"""Discipline handlers: HQ file flow, queue, decide/appeal (018; Phase 1 site-scoped)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from app.bot import site_session
from app.models.discipline import DisciplineCase, DisciplineStatus
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


def _discipline(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["discipline_service"]


def _render(case: DisciplineCase) -> str:
    lines = [
        f"*Discipline #{case.id}* - `{case.status}`",
        f"Subject: `{case.subject_chat_id}`",
        f"Summary: {case.summary}",
    ]
    if case.decision:
        lines.append(f"Decision: {case.decision}")
    if case.decision_note:
        lines.append(f"Note: {case.decision_note}")
    if case.appeal_note:
        lines.append(f"Appeal: {case.appeal_note}")
    return "\n".join(lines)


def _queue_buttons(chat_id: str, context, case: DisciplineCase, site: str):
    auth = _auth(context)
    buttons = []
    if case.status in (DisciplineStatus.FILED, DisciplineStatus.APPEALED) and \
            auth.has_capability(chat_id, "review_disciplinary_case", site):
        buttons.append(
            [InlineKeyboardButton("Take into review",
                                  callback_data=f"disc_review:{case.id}")])
    if case.status in (DisciplineStatus.UNDER_REVIEW, DisciplineStatus.APPEALED) and \
            auth.has_capability(chat_id, "approve_disciplinary_action", site) and \
            str(chat_id) != str(case.filed_by):
        buttons.append(
            [InlineKeyboardButton("Decide",
                                  callback_data=f"disc_decide:{case.id}")])
    return InlineKeyboardMarkup(buttons) if buttons else None


# --- filing (single-shot, like /assisted) ---

async def discipline_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """File a case: /discipline <subject_chat_id> <summary...>."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="discipline",
                                    hint="Re-send your /discipline command.")
        return
    if not _auth(context).has_capability(chat_id, "review_disciplinary_case", site):
        await update.effective_message.reply_text("Discipline filing is HQ-only.")
        return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text(
            "Usage: /discipline <subject_chat_id> <summary>")
        return
    _, subject, summary = parts
    try:
        case = _discipline(context).file(subject.strip(), chat_id, summary,
                                         site_id=site)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not file: {e}")
        return
    await update.effective_message.reply_text(
        f"Discipline case `#{case.id}` filed against `{subject.strip()}` "
        f"at `{site}`.", parse_mode="Markdown")
    from app.bot.notify import notify

    for reviewer in _auth(context).chat_ids_for_site(
            site, "review_disciplinary_case"):
        if str(reviewer) != str(chat_id):
            await notify(context, "case_update", reviewer, site,
                         reference=f"discipline:{case.id}",
                         date=(case.created_at or "")[:10],
                         kind="discipline", ref=case.id,
                         verdict="filed - needs triage",
                         note=summary[:120])


# --- queues ---

async def mydiscipline_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cases against the caller, across their own sites (SELF visibility)."""
    chat_id, _ = _me(update)
    auth = _auth(context)
    service = _discipline(context)
    rows = []
    for site in auth.sites_for_user(chat_id):
        rows.extend(service.list_against(chat_id, site_id=site))
    if not rows:
        await update.effective_message.reply_text("No discipline cases against you.")
        return
    for case in sorted(rows, key=lambda c: c.created_at)[:10]:
        markup = None
        if case.status == DisciplineStatus.DECIDED and \
                str(case.subject_chat_id) == chat_id:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("Appeal",
                                     callback_data=f"disc_appeal:{case.id}")]])
        await update.effective_message.reply_text(
            _render(case), parse_mode="Markdown", reply_markup=markup)


async def discipline_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="queue",
                                    hint="Press /discipline_queue again.")
        return
    if not _auth(context).has_capability(chat_id, "review_disciplinary_case", site):
        await update.effective_message.reply_text("Discipline queue is HQ-only.")
        return
    rows = _discipline(context).open_cases(site_id=site)
    if not rows:
        await update.effective_message.reply_text("Discipline queue is empty.")
        return
    for case in rows[:10]:
        await update.effective_message.reply_text(
            _render(case), parse_mode="Markdown",
            reply_markup=_queue_buttons(chat_id, context, case, site))


# --- callbacks ---

async def handle_discipline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id, _ = _me(update)
    service = _discipline(context)

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
        case_id = int(raw_id)
    except ValueError:
        return

    if action == "disc_review":
        case = service.get(case_id, site_id=site)
        if case is None or not _auth(context).has_capability(
                chat_id, "review_disciplinary_case", site):
            await query.edit_message_text("Review is HQ-only (check /site).")
            return
        try:
            case = service.review(case_id, chat_id, site_id=site)
        except DatabaseError as e:
            await query.edit_message_text(f"Could not review: {e}")
            return
        await query.edit_message_text(
            f"Case `#{case.id}` is now under review.", parse_mode="Markdown")
    elif action == "disc_decide":
        case = service.get(case_id, site_id=site)
        if case is None or not _auth(context).has_capability(
                chat_id, "approve_disciplinary_action", site):
            await query.edit_message_text("Decisions need HQ approval rights.")
            return
        if str(chat_id) == str(case.filed_by):
            await query.edit_message_text("Filer cannot decide their own filing.")
            return
        context.user_data["disc_decide_id"] = case_id
        context.user_data["disc_decide_site"] = site
        context.user_data["state"] = "awaiting_disc_note"
        await query.edit_message_text(
            "Send outcome and note as: `<outcome> | <note>`.")
    elif action == "disc_appeal":
        case = service.get(case_id, site_id=site)
        if case is None or str(case.subject_chat_id) != chat_id:
            await query.edit_message_text("Only the subject may appeal this case.")
            return
        context.user_data["disc_appeal_id"] = case_id
        context.user_data["disc_appeal_site"] = site
        context.user_data["state"] = "awaiting_disc_appeal"
        await query.edit_message_text("Send your appeal note.")


async def handle_disc_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if "|" not in text:
        await update.message.reply_text(
            "Send as: `<outcome> | <note>` (or /cancel).")
        return
    outcome, note = (part.strip() for part in text.split("|", 1))
    chat_id, _ = _me(update)
    try:
        case = _discipline(context).decide(
            context.user_data.get("disc_decide_id"), chat_id, outcome, note,
            site_id=context.user_data.get("disc_decide_site"))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not decide: {e}")
        return
    context.user_data.pop("disc_decide_id", None)
    context.user_data.pop("disc_decide_site", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Decided case `#{case.id}`: {outcome}.",
                                    parse_mode="Markdown")
    from app.bot.notify import notify

    await notify(context, "discipline_decision", case.subject_chat_id,
                 case.site_id, reference=f"discipline:{case.id}",
                 date=(case.created_at or "")[:10], ref=case.id,
                 verdict=outcome, note=note)


async def handle_disc_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    note = (update.message.text or "").strip()
    if not note:
        await update.message.reply_text("Send your appeal note as text.")
        return
    chat_id, _ = _me(update)
    try:
        case = _discipline(context).appeal(
            context.user_data.get("disc_appeal_id"), chat_id, note,
            site_id=context.user_data.get("disc_appeal_site"))
    except DatabaseError as e:
        await update.message.reply_text(f"Could not appeal: {e}")
        return
    context.user_data.pop("disc_appeal_id", None)
    context.user_data.pop("disc_appeal_site", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Appealed case `#{case.id}` - back to review.",
                                    parse_mode="Markdown")
    from app.bot.notify import notify

    for reviewer in _auth(context).chat_ids_for_site(
            case.site_id, "approve_disciplinary_action"):
        if str(reviewer) != str(chat_id):
            await notify(context, "case_update", reviewer, case.site_id,
                         reference=f"discipline:{case.id}",
                         date=(case.created_at or "")[:10],
                         kind="discipline", ref=case.id,
                         verdict="appealed - back to review", note=note)


def get_registration_handlers() -> list:
    return [
        CommandHandler("discipline", discipline_command),
        CommandHandler("mydiscipline", mydiscipline_command),
        CommandHandler("discipline_queue", discipline_queue_command),
        CallbackQueryHandler(handle_discipline_callback, pattern="^disc_"),
    ]
