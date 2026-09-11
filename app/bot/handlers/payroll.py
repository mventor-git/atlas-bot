"""Payroll handlers: runs, salaries, self-service (021)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.models.payroll import PayrollRunStatus
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


def _payroll(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["payroll_service"]


def _require_payroll(update: Update, context) -> str | None:
    chat_id, _ = _me(update)
    if not _auth(context).has_capability(chat_id, "manage_payroll"):
        return None
    return chat_id


def _render_run(run, lines) -> str:
    total = round(sum(line.net for line in lines), 2)
    head = [f"*Payroll {run.period}* - `{run.status}`",
            f"Lines: {len(lines)} | Total: {total:g}"]
    for line in lines[:20]:
        head.append(f"`{line.chat_id}`: base {line.base_pay:g} + OT "
                    f"{line.ot_amount:g} - adv {line.advances:g} - ded "
                    f"{line.deductions:g} = *{line.net:g}*")
    if len(lines) > 20:
        head.append(f"... and {len(lines) - 20} more")
    return "\n".join(head)


# --- officer commands ---

async def payroll_build_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open a draft run: /payroll_build <YYYY-MM>."""
    by = _require_payroll(update, context)
    if by is None:
        await update.effective_message.reply_text("Payroll runs are HQ-only.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /payroll_build <YYYY-MM>")
        return
    try:
        run = _payroll(context).create_run(parts[1], by)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not build: {e}")
        return
    await update.effective_message.reply_text(
        f"Draft payroll `{run.period}` opened (#{run.id}). "
        "Add lines with /payroll_add.", parse_mode="Markdown")


async def payroll_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a line: /payroll_add <YYYY-MM> <chat_id> [ot_hours] [advances] [deductions]."""
    if _require_payroll(update, context) is None:
        await update.effective_message.reply_text("Payroll runs are HQ-only.")
        return
    parts = (update.message.text or "").split()
    if len(parts) < 3:
        await update.effective_message.reply_text(
            "Usage: /payroll_add <YYYY-MM> <chat_id> [ot_hours] [advances] [deductions]")
        return
    try:
        numbers = [float(p) for p in parts[3:6]]
    except ValueError:
        await update.effective_message.reply_text("Numbers only for hours/amounts.")
        return
    while len(numbers) < 3:
        numbers.append(0.0)
    service = _payroll(context)
    run = service._repo.get_by_period(parts[1])
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    user = service._users.get_by_chat_id(parts[2])
    if user is None or user.monthly_salary is None:
        await update.effective_message.reply_text(
            f"No salary stored for `{parts[2]}` - set it with /salary first.",
            parse_mode="Markdown")
        return
    try:
        line = service.add_line(run.id, parts[2], user.monthly_salary,
                                *numbers)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not add: {e}")
        return
    await update.effective_message.reply_text(
        f"Line for `{parts[2]}`: net *{line.net:g}*.", parse_mode="Markdown")


async def payroll_view_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View a run: /payroll_view <YYYY-MM>."""
    if _require_payroll(update, context) is None:
        await update.effective_message.reply_text("Payroll runs are HQ-only.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /payroll_view <YYYY-MM>")
        return
    service = _payroll(context)
    run = service._repo.get_by_period(parts[1])
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    await update.effective_message.reply_text(
        _render_run(run, service._repo.lines_for(run.id)), parse_mode="Markdown")


async def payroll_export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lock a run at export: /payroll_export <YYYY-MM> (021b emits the PDF)."""
    if _require_payroll(update, context) is None:
        await update.effective_message.reply_text("Payroll runs are HQ-only.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /payroll_export <YYYY-MM>")
        return
    service = _payroll(context)
    run = service._repo.get_by_period(parts[1])
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    try:
        run = service.mark_exported(run.id)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not export: {e}")
        return
    await update.effective_message.reply_text(
        f"Payroll `{run.period}` exported and locked "
        f"({len(service._repo.lines_for(run.id))} lines). PDF in 021b.",
        parse_mode="Markdown")


# --- self-service ---

async def mypay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show my line: /mypay <YYYY-MM>."""
    chat_id, _ = _me(update)
    if not _auth(context).has_capability(chat_id, "view_own_payroll"):
        await update.effective_message.reply_text("Payroll view needs an approved account.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /mypay <YYYY-MM>")
        return
    service = _payroll(context)
    run = service._repo.get_by_period(parts[1])
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    mine = [line for line in service._repo.lines_for(run.id)
            if str(line.chat_id) == chat_id]
    if not mine:
        await update.effective_message.reply_text("No payroll line for you.")
        return
    (line,) = mine
    status = run.status
    if run.status != PayrollRunStatus.EXPORTED:
        status += " (provisional)"
    await update.effective_message.reply_text(
        f"*{run.period}* - `{status}`\nBase {line.base_pay:g} + OT "
        f"{line.ot_amount:g} - adv {line.advances:g} - ded "
        f"{line.deductions:g} = *{line.net:g}*.",
        parse_mode="Markdown")


# --- salaries ---

async def salary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a salary: /salary <chat_id> <amount>."""
    if _require_payroll(update, context) is None:
        await update.effective_message.reply_text("Salaries are HQ-only.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 3:
        await update.effective_message.reply_text("Usage: /salary <chat_id> <amount>")
        return
    try:
        amount = float(parts[2])
    except ValueError:
        await update.effective_message.reply_text("Amount must be a number.")
        return
    service = _payroll(context)
    try:
        user = service._users.set_salary(parts[1], amount)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not set: {e}")
        return
    if user is None:
        await update.effective_message.reply_text(f"Unknown user `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    await update.effective_message.reply_text(
        f"Salary for `{parts[1]}` set to {amount:g}.", parse_mode="Markdown")


async def salary_import_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a CSV paste: /salary_import, then send chat_id,salary lines."""
    if _require_payroll(update, context) is None:
        await update.effective_message.reply_text("Salaries are HQ-only.")
        return
    context.user_data["state"] = "awaiting_salary_csv"
    await update.effective_message.reply_text(
        "Send CSV lines as `chat_id,salary` (or /cancel).", parse_mode="Markdown")


async def handle_salary_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Send CSV lines (or /cancel).")
        return
    report = _payroll(context).import_salaries(text)
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"Salaries: {report['updated']} updated, {report['unknown']} unknown, "
        f"{report['skipped']} skipped.")


def get_registration_handlers() -> list:
    return [
        CommandHandler("payroll_build", payroll_build_command),
        CommandHandler("payroll_add", payroll_add_command),
        CommandHandler("payroll_view", payroll_view_command),
        CommandHandler("payroll_export", payroll_export_command),
        CommandHandler("mypay", mypay_command),
        CommandHandler("salary", salary_command),
        CommandHandler("salary_import", salary_import_command),
    ]
