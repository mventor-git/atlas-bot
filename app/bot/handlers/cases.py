"""Case handlers: file, queues, review/resolve/appeal (015, P6b)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from app.models.case import SUBMIT_CAPABILITY, Case, CaseStatus, CaseType
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# case type -> (review cap, resolve cap). Handlers gate; service enforces state.
REVIEW_CAPS = {
    CaseType.GRIEVANCE: ("review_grievance", "resolve_grievance"),
    CaseType.COMPLAINT: ("review_complaint", "resolve_complaint"),
    CaseType.SUGGESTION: ("review_suggestion", "resolve_suggestion"),
    CaseType.RESIGNATION: ("review_resignation", "review_resignation"),
}


# --- helpers ---

def _me(update: Update) -> tuple[str, str]:
    user = update.effective_user
    name = f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)
    return str(user.id), name


def _auth(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["authorization_service"]


def _cases(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["case_service"]


def _can_review(chat_id: str, context, case_type: str) -> bool:
    return _auth(context).has_capability(chat_id, REVIEW_CAPS[case_type][0])


def _can_resolve(chat_id: str, context, case_type: str) -> bool:
    return _auth(context).has_capability(chat_id, REVIEW_CAPS[case_type][1])


def _can_review_any(chat_id: str, context) -> bool:
    return any(
        _auth(context).has_capability(chat_id, caps[0])
        for caps in REVIEW_CAPS.values()
    )


async def _notify(context: ContextTypes.DEFAULT_TYPE, chat_id: str, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=int(chat_id), text=text,
                                       parse_mode="Markdown")
    except Exception as e:
        logger.warning("Could not notify %s: %s", chat_id, e)


def _render(case: Case) -> str:
    lines = [
        f"*{case.case_type.title()} #{case.id}* - `{case.status}`",
        f"Summary: {case.summary}",
    ]
    if case.resolution_note:
        lines.append(f"Resolution: {case.resolution_note}")
    if case.appeal_note:
        lines.append(f"Appeal: {case.appeal_note}")
    return "\n".join(lines)


def _queue_buttons(chat_id: str, context, case: Case):
    buttons = []
    if case.status in (CaseStatus.FILED, CaseStatus.APPEALED) and _can_review(
            chat_id, context, case.case_type):
        buttons.append(
            [InlineKeyboardButton("Take into review",
                                  callback_data=f"case_review:{case.id}")])
    if case.status in (CaseStatus.UNDER_REVIEW, CaseStatus.APPEALED) and _can_resolve(
            chat_id, context, case.case_type):
        buttons.append(
            [InlineKeyboardButton("Resolve",
                                  callback_data=f"case_resolve:{case.id}")])
    return InlineKeyboardMarkup(buttons) if buttons else None


# --- filing ---

async def _start_file(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      kind: str) -> None:
    chat_id, _ = _me(update)
    if not _auth(context).has_capability(chat_id, SUBMIT_CAPABILITY[kind]):
        await update.effective_message.reply_text("Your account cannot file this.")
        return
    context.user_data["case_flow"] = {"type": kind}
    context.user_data["state"] = "awaiting_case_summary"
    await update.effective_message.reply_text(
        f"Describe your {kind} in one message (or /cancel).")


async def grievance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_file(update, context, CaseType.GRIEVANCE)


async def complaint_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_file(update, context, CaseType.COMPLAINT)


async def suggestion_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_file(update, context, CaseType.SUGGESTION)


async def resign_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_file(update, context, CaseType.RESIGNATION)


async def handle_case_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Send the summary as text (or /cancel).")
        return
    chat_id, _ = _me(update)
    kind = (context.user_data.get("case_flow") or {}).get("type")
    try:
        case = _cases(context).file(chat_id, kind, text)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not file: {e}")
        return
    context.user_data.pop("case_flow", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"Filed {kind} `#{case.id}` - a reviewer will triage it.",
        parse_mode="Markdown")


# --- queues ---

async def mycases_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, _ = _me(update)
    rows = _cases(context)._repo.for_reporter(chat_id)
    if not rows:
        await update.effective_message.reply_text("You have no cases.")
        return
    for case in rows[:10]:
        markup = None
        if case.status == CaseStatus.RESOLVED and str(case.reporter_chat_id) == chat_id:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("Appeal",
                                     callback_data=f"case_appeal:{case.id}")]])
        await update.effective_message.reply_text(
            _render(case), parse_mode="Markdown", reply_markup=markup)


async def cases_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, _ = _me(update)
    if not _can_review_any(chat_id, context):
        await update.effective_message.reply_text("Review queue is for reviewers only.")
        return
    rows = _cases(context)._repo.open_cases()
    if not rows:
        await update.effective_message.reply_text("Review queue is empty.")
        return
    for case in rows[:10]:
        await update.effective_message.reply_text(
            _render(case), parse_mode="Markdown",
            reply_markup=_queue_buttons(chat_id, context, case))


# --- callbacks ---

async def handle_case_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id, _ = _me(update)
    service = _cases(context)

    parts = data.split(":")
    if len(parts) != 2:
        return
    action, raw_id = parts
    try:
        case_id = int(raw_id)
    except ValueError:
        return

    if action == "case_review":
        case = service._repo.get_by_id(case_id)
        if case is None or not _can_review(chat_id, context, case.case_type):
            await query.edit_message_text("Review needs a reviewer account.")
            return
        try:
            case = service.review(case_id, chat_id)
        except DatabaseError as e:
            await query.edit_message_text(f"Could not review: {e}")
            return
        await query.edit_message_text(
            f"Case `#{case.id}` is now under review.", parse_mode="Markdown")
        await _notify(context, case.reporter_chat_id,
                      f"Your {case.case_type} `#{case.id}` is under review.")
    elif action == "case_resolve":
        case = service._repo.get_by_id(case_id)
        if case is None or not _can_resolve(chat_id, context, case.case_type):
            await query.edit_message_text("Resolution needs a reviewer account.")
            return
        context.user_data["case_resolve_id"] = case_id
        context.user_data["state"] = "awaiting_case_note"
        await query.edit_message_text("Send the resolution note.")
    elif action == "case_appeal":
        case = service._repo.get_by_id(case_id)
        if case is None or str(case.reporter_chat_id) != chat_id:
            await query.edit_message_text("Only the filer may appeal this case.")
            return
        context.user_data["case_appeal_id"] = case_id
        context.user_data["state"] = "awaiting_case_appeal"
        await query.edit_message_text("Send your appeal note.")


async def handle_case_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    note = (update.message.text or "").strip()
    if not note:
        await update.message.reply_text("Send the resolution note as text.")
        return
    chat_id, _ = _me(update)
    try:
        case = _cases(context).resolve(
            context.user_data.get("case_resolve_id"), chat_id, note)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not resolve: {e}")
        return
    context.user_data.pop("case_resolve_id", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Resolved case `#{case.id}`.",
                                    parse_mode="Markdown")
    await _notify(context, case.reporter_chat_id,
                  f"Your {case.case_type} `#{case.id}` was resolved: {note}")


async def handle_case_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    note = (update.message.text or "").strip()
    if not note:
        await update.message.reply_text("Send your appeal note as text.")
        return
    chat_id, _ = _me(update)
    try:
        case = _cases(context).appeal(
            context.user_data.get("case_appeal_id"), chat_id, note)
    except DatabaseError as e:
        await update.message.reply_text(f"Could not appeal: {e}")
        return
    context.user_data.pop("case_appeal_id", None)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"Appealed case `#{case.id}` - back to review.",
                                    parse_mode="Markdown")


def get_registration_handlers() -> list:
    return [
        CommandHandler("grievance", grievance_command),
        CommandHandler("complaint", complaint_command),
        CommandHandler("suggestion", suggestion_command),
        CommandHandler("resign", resign_command),
        CommandHandler("mycases", mycases_command),
        CommandHandler("cases", cases_command),
        CallbackQueryHandler(handle_case_callback, pattern="^case_"),
    ]
