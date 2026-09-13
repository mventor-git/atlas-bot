"""Notification message catalog (031): content builders per named type.

Scheduling and eligibility live elsewhere; this module owns WORDS only.
Builders take plain values (ids, dates, counts) - never user objects -
and return Markdown-safe text. Handlers must not duplicate these strings.
"""

from __future__ import annotations


def _esc(value) -> str:
    return str(value or "").replace("*", "").replace("_", " ").strip()


def build(ntype: str, **kw) -> str:
    """Render the message for a notification type. Unknown types raise."""
    try:
        return _BUILDERS[ntype](**kw)
    except KeyError:
        raise ValueError(f"Unknown notification type: {ntype}") from None


def _report_missing(**kw):
    return (
        "\U0001f4cb *Daily report missing*\n\n"
        f"Site `{_esc(kw.get('site'))}` has no report for "
        f"`{_esc(kw.get('date'))}` yet.\n"
        "File it with /new before the deadline.")


def _report_review(**kw):
    return (
        "\U0001f4cb *Report ready for review*\n\n"
        f"Site `{_esc(kw.get('site'))}` report for `{_esc(kw.get('date'))}` "
        "is FINAL.\nApprove with /approve or reject with /reject + reason.")


def _report_rejected(**kw):
    return (
        "\U0001f501 *Your report was rejected*\n\n"
        f"Date `{_esc(kw.get('date'))}`.\n"
        f"Reason: {_esc(kw.get('reason'))}\n"
        "Fix it, then /resubmit.")


def _report_resubmitted(**kw):
    return (
        "\U0001f4cb *Report resubmitted*\n\n"
        f"Site `{_esc(kw.get('site'))}` report for `{_esc(kw.get('date'))}` "
        "is back for review.")


def _report_approved(**kw):
    return (
        "\u2705 *Your report was approved*\n\n"
        f"Date `{_esc(kw.get('date'))}` at `{_esc(kw.get('site'))}`.")


def _attendance_pending(**kw):
    return (
        "\U0001f464 *Attendance needs confirmation*\n\n"
        f"`{_esc(kw.get('subject'))}` checked in at `{_esc(kw.get('site'))}` "
        f"({kw.get('kind', 'in')}, {kw.get('evidence', 'unchecked')}).\n"
        "Review it in /attendance_queue.")


def _attendance_update(**kw):
    return (
        "\U0001f464 *Attendance update*\n\n"
        f"Your check-{_esc(kw.get('kind'))} for `{_esc(kw.get('date'))}` "
        f"was {_esc(kw.get('verdict'))}.")


def _claim_decision(**kw):
    return (
        "\U0001f4dd *Claim {verb}*\n\n"
        f"Your attendance claim for `{_esc(kw.get('date'))}` was "
        f"{_esc(kw.get('verdict'))}: {_esc(kw.get('note'))}".format(
            verb="decided"))


def _overtime_pending(**kw):
    return (
        "\u23f0 *Overtime request pending*\n\n"
        f"`{_esc(kw.get('subject'))}` requests {kw.get('hours', '?')}h on "
        f"`{_esc(kw.get('date'))}` ({_esc(kw.get('site'))}).\n"
        "Confirm or reject it in /hr_pending.")


def _leave_decision(**kw):
    return (
        "\U0001f4c5 *Leave request {verb}*\n\n"
        f"{_esc(kw.get('kind'))} `{_esc(kw.get('date'))}`: "
        f"{_esc(kw.get('verdict'))}. {_esc(kw.get('note'))}".format(
            verb="update"))


def _case_update(**kw):
    return (
        "\U0001f4e9 *Case update*\n\n"
        f"Your {_esc(kw.get('kind'))} `#{_esc(kw.get('ref'))}` is now "
        f"`{_esc(kw.get('verdict'))}`. {_esc(kw.get('note'))}")


def _discipline_decision(**kw):
    return (
        "\u26a0\ufe0f *Disciplinary decision*\n\n"
        f"Case `#{_esc(kw.get('ref'))}` decided: {_esc(kw.get('verdict'))} - "
        f"{_esc(kw.get('note'))}")


def _payroll_ready(**kw):
    return (
        "\U0001f4b0 *Payroll ready*\n\n"
        f"Period `{_esc(kw.get('period'))}` at `{_esc(kw.get('site'))}` "
        "was exported and locked.")


def _payroll_exception(**kw):
    return (
        "\u26a0\ufe0f *Payroll exception - review required*\n\n"
        f"Period `{_esc(kw.get('period'))}` at `{_esc(kw.get('site'))}`: "
        f"{_esc(kw.get('note'))}")


def _payroll_corrected(**kw):
    return (
        "\U0001f4b0 *Payroll correction recorded*\n\n"
        f"Period `{_esc(kw.get('period'))}` at `{_esc(kw.get('site'))}`: "
        f"{_esc(kw.get('note'))}")


def _morning(**kw):
    return (
        "\U0001f305 *Good Morning!*\n\n"
        "A new workday has started. Would you like to create today's labor report?\n\n"
        "Use /new to start fresh, or /copy to copy yesterday's report.")


def _late_morning(**kw):
    return (
        "\u23f0 *Reminder*\n\n"
        "Today's labor report hasn't been created yet.\n"
        "The deadline is at *2:00 PM*.\n\n"
        "Use /new to create it now, or /start for the dashboard.")


def _afternoon(**kw):
    return (
        "\u26a0\ufe0f *Final Reminder*\n\n"
        "The submission deadline is approaching!\n"
        "Today's labor report is still missing.\n\n"
        "Use /new to create it now before the deadline passes.")


def _no_report_created(**kw):
    return (
        "\U0001f4cb *End of Workday*\n\n"
        "No labor report was created for today.\n"
        "An empty day record has been saved.\n\n"
        "If this is a mistake, use /new to create a report or contact your admin.")


_BUILDERS = {
    "morning": _morning,
    "late_morning": _late_morning,
    "afternoon": _afternoon,
    "no_report_created": _no_report_created,
    "report_missing": _report_missing,
    "report_review": _report_review,
    "report_rejected": _report_rejected,
    "report_resubmitted": _report_resubmitted,
    "report_approved": _report_approved,
    "attendance_pending": _attendance_pending,
    "attendance_update": _attendance_update,
    "claim_decision": _claim_decision,
    "overtime_pending": _overtime_pending,
    "leave_decision": _leave_decision,
    "case_update": _case_update,
    "discipline_decision": _discipline_decision,
    "payroll_ready": _payroll_ready,
    "payroll_exception": _payroll_exception,
    "payroll_corrected": _payroll_corrected,
}

TYPES = tuple(sorted(_BUILDERS))
