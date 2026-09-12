"""Active-site session context (Phase 1 tenancy hardening).

The ONLY sanctioned way for handlers to pick a site:

1. The user's session choice (``user_data["active_site"]``).
2. Validated server-side against ACTIVE memberships (user_site_memberships).
3. Single membership -> auto-bound. Multi + unset -> selection required.

The process SITE_ID is never consulted here: it is only a deployment
default used by system jobs (notifications/origins), never a user context.

Usage in a handler:

    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume="cases",
                                    hint="Repeat /grievance after picking.")
        return
    ...service.call(..., site_id=site)  / has_capability(..., site_id=site)

Register ``site_session.get_handlers()`` once at bot build.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from app.utils.logger import get_logger

logger = get_logger(__name__)

PICK_PREFIX = "sitepick:"


def _auth(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["authorization_service"]


def _chat_id(update: Update) -> str:
    return str(update.effective_user.id)


def resolve_site(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User's validated active site, or None when they must pick."""
    auth = _auth(context)
    return auth.resolve_active_site(_chat_id(update),
                                    context.user_data.get("active_site"))


async def ask_site(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   resume: str, hint: str = "Repeat your last command.") -> None:
    """Offer the user their own sites; store resume token + hint."""
    auth = _auth(context)
    chat_id = _chat_id(update)
    sites = auth.sites_for_user(chat_id)
    message = update.effective_message
    if not sites:
        await message.reply_text(
            "You have no site membership - ask an admin (/hr_member).")
        return
    if len(sites) == 1:  # resolve_active_site should have auto-bound; be safe
        context.user_data["active_site"] = sites[0]
        return
    context.user_data["site_pick_resume"] = resume
    context.user_data["site_pick_hint"] = hint
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(s, callback_data=f"{PICK_PREFIX}{s}")]
        for s in sites
    ])
    await message.reply_text("Which site?", reply_markup=keyboard)


async def site_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/site — show or switch the caller's active site (validated)."""
    auth = _auth(context)
    chat_id = _chat_id(update)
    sites = auth.sites_for_user(chat_id)
    args = context.args or []
    if not sites:
        await update.effective_message.reply_text(
            "You have no site membership - ask an admin (/hr_member).")
        return
    if not args:
        current = context.user_data.get("active_site")
        lines = [f"Your sites: {', '.join(sites)}"]
        lines.append(f"Active: {current or auth.resolve_active_site(chat_id, None)}")
        lines.append("Switch with: /site <name>")
        await update.effective_message.reply_text("\n".join(lines))
        return
    wanted = args[0]
    if wanted not in sites:
        await update.effective_message.reply_text(
            f"Not a member of `{wanted}`. Your sites: {', '.join(sites)}.")
        return
    context.user_data["active_site"] = wanted
    await update.effective_message.reply_text(f"Active site: `{wanted}`.")


async def handle_site_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith(PICK_PREFIX):
        return
    site = data[len(PICK_PREFIX):]
    chat_id = _chat_id(update)
    auth = _auth(context)
    # Never trust the callback payload: validate against memberships.
    if site not in auth.sites_for_user(chat_id):
        await query.edit_message_text("Not a member of that site.")
        return
    context.user_data["active_site"] = site
    hint = context.user_data.pop("site_pick_hint", "")
    context.user_data.pop("site_pick_resume", None)
    await query.edit_message_text(f"Active site: `{site}`. {hint}".strip(),
                                  parse_mode="Markdown")


def get_handlers() -> list:
    from telegram.ext import CommandHandler

    return [
        CommandHandler("site", site_command),
        CallbackQueryHandler(handle_site_pick, pattern=f"^{PICK_PREFIX}"),
    ]
