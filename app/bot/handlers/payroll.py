"""Payroll handlers: runs, salaries, policy, corrections, self-service."""

from __future__ import annotations

from datetime import datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.bot import site_session
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


def _audit(context, actor: str, action: str, object_type: str,
           object_id=None, object_date: str = "",
           old_value: str = "", new_value: str = "") -> None:
    """Best-effort domain audit via EventLogService (never breaks ops)."""
    try:
        svc = context.bot_data.get("event_log_service")
        if svc is None:
            return
        svc.log(actor, action, object_type=object_type, object_id=object_id,
                object_date=object_date, old_value=old_value,
                new_value=new_value)
    except Exception as e:  # audit must not break payroll operations
        logger.warning("Payroll audit log failed: %s", e)


async def _notify_holders(context, ntype: str, site: str, period: str,
                          note: str = "") -> None:
    """Durable outbox fan-out to site payroll holders (Stage 4)."""
    from app.bot.notify import notify

    auth = _auth(context)
    for holder in auth.chat_ids_for_site(site, "manage_payroll"):
        await notify(context, ntype, holder, site,
                     reference=f"payroll:{period}", date=period,
                     period=period, site=site, note=note)


async def _officer(update: Update, context, resume: str) -> tuple[str, str] | None:
    """Resolve active site + manage_payroll gate; None = handled (asked/denied)."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=resume,
                                    hint="Repeat your payroll command.")
        return None
    if not _auth(context).has_capability(chat_id, "manage_payroll", site):
        await update.effective_message.reply_text("Payroll needs HQ rights.")
        return None
    return chat_id, site


def _render_run(run, lines) -> str:
    total = round(sum(line.net for line in lines), 2)
    head = [f"*Payroll {run.period}* - `{run.status}` - site `{run.site_id}`",
            f"Lines: {len(lines)} | Total: {total:g}"]
    for line in lines[:20]:
        flag = " \u26a0\ufe0fREVIEW" if line.net < 0 else ""
        head.append(f"`{line.chat_id}`: base {line.base_pay:g} + OT "
                    f"{line.ot_amount:g} - adv {line.advances:g} - ded "
                    f"{line.deductions:g} = *{line.net:g}*{flag}")
    if len(lines) > 20:
        head.append(f"... and {len(lines) - 20} more")
    negatives = sum(1 for line in lines if line.net < 0)
    if negatives:
        head.append(f"\u26a0\ufe0f {negatives} negative net(s) require review "
                    "(no floor applied; policy OPEN).")
    return "\n".join(head)


# --- officer commands ---

async def payroll_build_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open a draft run: /payroll_build <YYYY-MM>."""
    officer = await _officer(update, context, "payroll_build")
    if officer is None:
        return
    by, site = officer
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /payroll_build <YYYY-MM>")
        return
    try:
        run = _payroll(context).create_run(parts[1], by, site_id=site)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not build: {e}")
        return
    _audit(context, by, "payroll.run_created", "payroll_run",
           object_id=run.id, object_date=run.period)
    await update.effective_message.reply_text(
        f"Draft payroll `{run.period}` opened at `{site}` (#{run.id}). "
        "Add lines with /payroll_add.", parse_mode="Markdown")


async def payroll_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a line: /payroll_add <YYYY-MM> <chat_id> [ot_hours] [advances] [deductions]."""
    officer = await _officer(update, context, "payroll_add")
    if officer is None:
        return
    _, site = officer
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
    run = service.get_run(parts[1], site_id=site)
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    salary = service.salary_for_period(parts[2], run.period)
    if salary is None:
        await update.effective_message.reply_text(
            f"No salary effective for `{parts[2]}` in `{run.period}` - "
            "set it with /salary first.", parse_mode="Markdown")
        return
    try:
        line = service.add_line(run.id, parts[2], salary["amount"], *numbers,
                                site_id=site)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not add: {e}")
        return
    _audit(context, officer[0], "payroll.line_added", "payroll_line",
           object_id=line.id, object_date=run.period,
           new_value=f"{parts[2]} net={line.net:g} base={line.base_pay:g}")
    text = f"Line for `{parts[2]}`: net *{line.net:g}*."
    if line.net < 0:
        text += ("\n\u26a0\ufe0f Payroll exception: negative net requires "
                 "review (no floor applied).")
        await _notify_holders(context, "payroll_exception", site, run.period,
                              f"negative net for `{parts[2]}`: {line.net:g}")
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def payroll_view_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View a run: /payroll_view <YYYY-MM>."""
    officer = await _officer(update, context, "payroll_view")
    if officer is None:
        return
    _, site = officer
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /payroll_view <YYYY-MM>")
        return
    service = _payroll(context)
    run = service.get_run(parts[1], site_id=site)
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    await update.effective_message.reply_text(
        _render_run(run, service.lines(run)), parse_mode="Markdown")


async def payroll_export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lock a run at export (lock + notify; document artifact deferred P5-2)."""
    officer = await _officer(update, context, "payroll_export")
    if officer is None:
        return
    chat_id, site = officer
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /payroll_export <YYYY-MM>")
        return
    service = _payroll(context)
    run = service.get_run(parts[1], site_id=site)
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    try:
        run = service.mark_exported(run.id, site_id=site)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not export: {e}")
        return
    lines = service.lines(run)
    negatives = sum(1 for line in lines if line.net < 0)
    await update.effective_message.reply_text(
        f"Payroll `{run.period}` exported and locked "
        f"({len(lines)} lines). Document artifact deferred (P5-2).",
        parse_mode="Markdown")
    _audit(context, chat_id, "payroll.run_exported", "payroll_run",
           object_id=run.id, object_date=run.period,
           new_value=f"{len(lines)} lines locked")
    from app.bot.notify import notify

    auth = _auth(context)
    for holder in auth.chat_ids_for_site(site, "manage_payroll"):
        if str(holder) != str(chat_id):
            await notify(context, "payroll_ready", holder, site,
                         reference=f"payroll:{run.period}",
                         date=run.period, period=run.period)
    if negatives:
        await _notify_holders(context, "payroll_exception", site, run.period,
                              f"{negatives} negative net(s) locked; review.")


# --- self-service ---

async def mypay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show my line(s): /mypay <YYYY-MM> (SELF-scoped, capability-gated)."""
    chat_id, _ = _me(update)
    auth = _auth(context)
    sites = [site for site in auth.sites_for_user(chat_id)
             if auth.has_capability(chat_id, "view_own_payroll", site)]
    if not sites:
        await update.effective_message.reply_text("Payroll view needs an approved account.")
        return
    parts = (update.message.text or "").split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Usage: /mypay <YYYY-MM>")
        return
    service = _payroll(context)
    mine, statuses = [], []
    for site in sites:   # SELF: own lines at capable sites only
        run = service.get_run(parts[1], site_id=site)
        if run is None:
            continue
        line = next((l for l in service.lines(run)
                     if str(l.chat_id) == chat_id), None)
        if line is not None:
            mine.append((run, line))
            statuses.append(run.status)
    if not mine:
        await update.effective_message.reply_text("No payroll line for you.")
        return
    for run, line in mine:
        status = run.status
        if run.status != PayrollRunStatus.EXPORTED:
            status += " (provisional)"
        text = (f"*{run.period}* at `{run.site_id}` - `{status}`\nBase "
                f"{line.base_pay:g} + OT {line.ot_amount:g} - adv "
                f"{line.advances:g} - ded {line.deductions:g} = *{line.net:g}*.")
        adjustments = service.adjustments_for(run.id, chat_id,
                                              site_id=run.site_id)
        if adjustments:
            total = round(sum(a.amount for a in adjustments), 2)
            text += (f"\nCorrections: {total:+g} -> *{line.net + total:g}* "
                     f"({len(adjustments)} authorized).")
        await update.effective_message.reply_text(text,
                                                  parse_mode="Markdown")
    if len(mine) > 1:
        combined = round(sum(
            service.adjusted_net(run.id, chat_id, site_id=run.site_id)
            for run, _ in mine), 2)
        await update.effective_message.reply_text(
            f"Combined across {len(mine)} sites: *{combined:g}*.",
            parse_mode="Markdown")


async def salary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a salary: /salary <chat_id> <amount>."""
    officer = await _officer(update, context, "salary")
    if officer is None:
        return
    by, _ = officer
    parts = (update.message.text or "").split()
    if len(parts) != 3:
        await update.effective_message.reply_text("Usage: /salary <chat_id> <amount>")
        return
    try:
        amount = float(parts[2])
        user = _payroll(context).set_salary(parts[1], amount, set_by=by)
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
    if await _officer(update, context, "salary_import") is None:
        return
    context.user_data["state"] = "awaiting_salary_csv"
    await update.effective_message.reply_text(
        "Send CSV lines as `chat_id,salary` (or /cancel).", parse_mode="Markdown")


async def handle_salary_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Send CSV lines (or /cancel).")
        return
    by, _ = _me(update)
    report = _payroll(context).import_salaries(text, set_by=by)
    context.user_data.pop("state", None)
    await update.message.reply_text(
        f"Salaries: {report['updated']} updated, {report['unknown']} unknown, "
        f"{report['skipped']} skipped.")


# --- policy + corrections (5B) ---

async def _policy_officer(update: Update, context, resume: str):
    """Resolve active site + manage_payroll_policy gate."""
    chat_id, _ = _me(update)
    site = site_session.resolve_site(update, context)
    if site is None:
        await site_session.ask_site(update, context, resume=resume,
                                    hint="Repeat your payroll command.")
        return None
    if not _auth(context).has_capability(chat_id, "manage_payroll_policy",
                                         site):
        await update.effective_message.reply_text(
            "Payroll policy needs HQ policy rights.")
        return None
    return chat_id, site


def _render_policy(site: str, period: str, policy) -> str:
    if policy is None:
        return (f"No policy at `{site}` for `{period}` - "
                "system defaults apply (240h / 1.5x / 2dp).")
    return (f"*Policy v{policy.version}* at `{site}` "
            f"(effective {policy.effective_from})\n"
            f"Basis: {policy.hours_basis} {policy.standard_hours:g}h | "
            f"OT x{policy.ot_multiplier:g} | round {policy.rounding}\n"
            f"Set by `{policy.set_by}`"
            + (f" - {policy.reason}" if policy.reason else ""))


async def payroll_policy_command(update: Update, context) -> None:
    """View the active policy: /payroll_policy [YYYY-MM]."""
    officer = await _officer(update, context, "payroll_policy")
    if officer is None:
        return
    _, site = officer
    parts = (update.message.text or "").split()
    period = parts[1] if len(parts) == 2 else datetime.now().strftime("%Y-%m")
    await update.effective_message.reply_text(
        _render_policy(site, period,
                       _payroll(context).active_policy(site, period)),
        parse_mode="Markdown")


async def payroll_policy_set_command(update: Update, context) -> None:
    """Set a policy version: /payroll_policy_set <YYYY-MM-DD> <hours> <ot_mult> [reason]."""
    officer = await _policy_officer(update, context, "payroll_policy_set")
    if officer is None:
        return
    by, site = officer
    parts = (update.message.text or "").split(None, 4)
    if len(parts) < 4:
        await update.effective_message.reply_text(
            "Usage: /payroll_policy_set <YYYY-MM-DD> <hours> <ot_mult> [reason]")
        return
    try:
        hours, mult = float(parts[2]), float(parts[3])
    except ValueError:
        await update.effective_message.reply_text("Hours and multiplier must be numbers.")
        return
    try:
        policy = _payroll(context).set_policy(
            site, by, standard_hours=hours, ot_multiplier=mult,
            effective_from=parts[1],
            reason=parts[4] if len(parts) > 4 else None)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not set: {e}")
        return
    await update.effective_message.reply_text(
        f"Policy v{policy.version} recorded for `{site}` "
        f"(effective {policy.effective_from}).",
        parse_mode="Markdown")
    _audit(context, by, "payroll.policy_set", "payroll_policy",
           object_id=policy.id, object_date=policy.effective_from,
           new_value=f"v{policy.version} {policy.standard_hours:g}h "
                     f"x{policy.ot_multiplier:g} {policy.rounding}")


async def payroll_adjust_command(update: Update, context) -> None:
    """Correct an exported run: /payroll_adjust <YYYY-MM> <chat_id> <amount> <reason>."""
    officer = await _officer(update, context, "payroll_adjust")
    if officer is None:
        return
    by, site = officer
    parts = (update.message.text or "").split(None, 4)
    if len(parts) < 5:
        await update.effective_message.reply_text(
            "Usage: /payroll_adjust <YYYY-MM> <chat_id> <amount> <reason>")
        return
    try:
        amount = float(parts[3])
    except ValueError:
        await update.effective_message.reply_text("Amount must be a number.")
        return
    service = _payroll(context)
    run = service.get_run(parts[1], site_id=site)
    if run is None:
        await update.effective_message.reply_text(f"No run for `{parts[1]}`.",
                                                  parse_mode="Markdown")
        return
    try:
        adj = service.add_adjustment(run.id, parts[2], amount, parts[4], by,
                                     site_id=site)
    except DatabaseError as e:
        await update.effective_message.reply_text(f"Could not adjust: {e}")
        return
    await update.effective_message.reply_text(
        f"Correction {amount:+g} recorded for `{parts[2]}` "
        f"(adjusted net *{service.adjusted_net(run.id, parts[2], site_id=site):g}*).",
        parse_mode="Markdown")
    _audit(context, by, "payroll.adjustment", "payroll_adjustment",
           object_id=adj.id, object_date=run.period,
           new_value=f"{parts[2]} {amount:+g} ({parts[4]})")
    await _notify_holders(context, "payroll_corrected", site, run.period,
                          f"{amount:+g} for `{parts[2]}`: {parts[4]}")


async def payroll_runs_command(update: Update, context) -> None:
    """List site runs: /payroll_runs (HQ inspection)."""
    officer = await _officer(update, context, "payroll_runs")
    if officer is None:
        return
    _, site = officer
    service = _payroll(context)
    runs = service.list_runs(site_id=site)
    if not runs:
        await update.effective_message.reply_text(f"No payroll runs at `{site}`.",
                                                  parse_mode="Markdown")
        return
    rows = []
    for run in runs:
        lines = service.lines(run)
        negs = sum(1 for line in lines if line.net < 0)
        flag = " \u26a0\ufe0fREVIEW" if negs else ""
        rows.append(f"`{run.period}` {run.status} "
                    f"({len(lines)} lines{flag})")
    await update.effective_message.reply_text(
        f"*Payroll runs at `{site}`*\n" + "\n".join(rows),
        parse_mode="Markdown")


def get_registration_handlers() -> list:
    return [
        CommandHandler("payroll_build", payroll_build_command),
        CommandHandler("payroll_add", payroll_add_command),
        CommandHandler("payroll_view", payroll_view_command),
        CommandHandler("payroll_export", payroll_export_command),
        CommandHandler("mypay", mypay_command),
        CommandHandler("salary", salary_command),
        CommandHandler("salary_import", salary_import_command),
        CommandHandler("payroll_policy", payroll_policy_command),
        CommandHandler("payroll_policy_set", payroll_policy_set_command),
        CommandHandler("payroll_adjust", payroll_adjust_command),
        CommandHandler("payroll_runs", payroll_runs_command),
    ]
