"""Report visibility boundary (029, Stage 2): the SOLE rule deciding which
representation of a daily report a caller may receive.

Audiences:
- HQ    : view_hq_reports at ANY own site -> detailed, any authorized site.
- SITE  : member of the report's site + create/approve_daily_report there ->
          detailed, own site (creators and reviewers must see content).
- OWNER : member of the report's site without report caps -> SIMPLE text
          of FINAL+ records only; drafts hidden; no files.
- NONE  : deny.

SIMPLE_FIELDS (owner-adjustable here, no code elsewhere):
date, day, site, status, contractor_count, total_workers,
finalized/approved/locked dates. NEVER: contractor names, zones, types,
details, craftsmen/helpers splits, creator identity, review notes.

The same rule gates text, PDF/file delivery, search detail, comparison,
contractor history, and exports. Dashboard-style bare counts stay open
(no names, details, or creator derivable from counts - §29 reasoning).
"""

from __future__ import annotations

HQ = "hq"
SITE = "site"
OWNER = "owner"
NONE = "none"

# Statuses an OWNER may see at all (official records only, never drafts).
OWNER_STATUSES = frozenset({"final", "approved", "locked", "no_report"})


def resolve(auth, chat_id: str, report_site: str | None) -> str:
    """Audience of a caller for one report's site. Never raises."""
    try:
        own = auth.sites_for_user(str(chat_id)) or []
    except Exception:
        return NONE
    if not report_site or report_site not in own:
        # HQ-wide power is checked across the caller's OWN sites only:
        # a capability row at another site is never consulted blindly.
        try:
            if any(auth.has_capability(str(chat_id), "view_hq_reports", s)
                   for s in own):
                return HQ
        except Exception:
            pass
        return NONE
    try:
        if auth.has_capability(str(chat_id), "view_hq_reports", report_site):
            return HQ
        if auth.has_capability(str(chat_id), "create_daily_report", report_site) \
                or auth.has_capability(str(chat_id), "approve_daily_report",
                                       report_site):
            return SITE
    except Exception:
        return NONE
    return OWNER


def can_see_detail(audience: str) -> bool:
    return audience in (HQ, SITE)


def owner_may_see_status(status_value: str | None) -> bool:
    return (status_value or "") in OWNER_STATUSES


def render_simple(report) -> str:
    """Totals-only representation. Field set fixed here (see module doc)."""
    items = report.items or []
    total = sum((i.workers or 0) for i in items)
    lines = [
        f"\U0001f4cb *Report: {report.date}* (`{report.site_id}`)",
        f"Day: {report.day}",
        f"Status: *{report.status.value if report.status else 'N/A'}*",
        f"Contractors: {len(items)}",
        f"Total Workers: {total}",
    ]
    if getattr(report, "finalized_at", None):
        lines.append(f"Finalized: {report.finalized_at[:10]}")
    if getattr(report, "approved_at", None):
        lines.append(f"Approved: {report.approved_at[:10]}")
    if getattr(report, "locked_at", None):
        lines.append(f"Locked: {report.locked_at[:10]}")
    lines.append("\n_Detailed breakdown available to reviewers._")
    return "\n".join(lines)


def detail_denied_message() -> str:
    return ("Detailed breakdown needs reviewer access. "
            "Showing the authorized summary instead.")
