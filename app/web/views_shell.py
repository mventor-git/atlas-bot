"""App shell: home + honest placeholders (ticket-037).

Placeholders never pretend to be implemented: each names the backend
that will back it (or states that none exists yet, e.g. warnings).
"""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, url_for

from app.web import authz
from app.web import i18n

bp = Blueprint("shell", __name__)

PLACEHOLDERS = {
    # slug: (title_en, title_ar, body_en, body_ar, backend)
    "projects": ("Projects / Sites", "المشاريع / المواقع",
                 "Site list, status and per-site summaries backed by config + memberships.",
                 "قائمة المواقع وحالتها وملخصاتها.",
                 "DEV sites page (this ticket)"),
    "reports": ("Daily Reports", "التقارير اليومية",
                "Site-scoped report browser with the same visibility rules as Telegram.",
                "متصفح تقارير بحدود الظهور نفسها.",
                "ReportRepository + report_visibility (wiring planned)"),
    "approvals": ("Approvals", "الاعتمادات",
                  "Report approval queue reusing ReportWorkflowService transitions.",
                  "طابور اعتماد التقارير.",
                  "ReportWorkflowService (wiring planned)"),
    "attendance": ("Attendance", "الحضور",
                   "Day queue, evidence review and verdicts via AttendanceDayService.",
                   "طابور الأيام ومراجعة الأدلة.",
                   "AttendanceDayService (wiring planned)"),
    "notifications": ("Notifications", "الإشعارات",
                      "Outbox-backed notice history per site.",
                      "سجل الإشعارات لكل موقع.",
                      "NotificationOutbox (read API exists)"),
    "documents": ("Documents", "المستندات",
                  "Generated PDFs/letters with print-queue state.",
                  "المستندات وحالة الطباعة.",
                      "exports + print queue (wiring planned)"),
    "stats": ("Reports", "التقارير",
              "Aggregates only from approved records; privacy minimums apply.",
              "إحصاءات من السجلات المعتمدة فقط.",
              "statistics engine (wiring planned)"),
    "chat": ("Atlas Chat", "محادثة أطلس",
             "Assistive chat. Never a security authority; backend authz decides.",
             "مساعدة فقط وليست مرجع صلاحيات.",
             "No backend yet — planned"),
}


@bp.get("/")
@authz.login_required
def home():
    from flask import session as _session
    from app.web.setup import get_manual_ack, setup_status
    from flask import current_app as _app
    try:
        s = _app.extensions["atlas_services"]
        chat_id = authz.current_chat_id()
        st = setup_status(s, chat_id, get_manual_ack(s["db"], chat_id))
        if not st["setup_complete"] and not _session.get("setup_redirected"):
            _session["setup_redirected"] = True
            return redirect(url_for("setup.wizard"))
    except Exception:
        pass
    return render_template("home.html")


def module_url(slug: str) -> str:
    """Single URL map for module slugs (sidebar, home cards)."""
    from flask import url_for
    if slug == "today":
        return url_for("today.board")
    if slug == "projects":
        return url_for("dev.sites")
    if slug == "approvals":
        return url_for("hr.queue")
    if slug == "notifications":
        return url_for("dev.notify_rules")
    if slug == "hr":
        return url_for("hr.dashboard")
    if slug == "config":
        return url_for("dev.config_home")
    if slug == "audit":
        return url_for("dev.audit_view")
    if slug in ("employees", "grievances", "warnings"):
        return url_for("hr." + slug)
    if slug == "setup":
        return url_for("setup.wizard")
    return url_for("shell.module_page", slug=slug)


@bp.get("/m/<slug>")
@authz.login_required
def module_page(slug: str):
    if slug == "hr":
        return redirect(url_for("hr.dashboard"))
    if slug in ("config", "audit"):
        return redirect(url_for("dev." + ("config_home" if slug == "config"
                                          else "audit_view")))
    if slug in ("employees", "grievances", "warnings"):
        return redirect(url_for("hr." + slug))
    if slug in ("projects",):
        return redirect(url_for("dev.sites"))
    if slug in ("approvals",):
        return redirect(url_for("hr.queue"))
    if slug in ("notifications",):
        return redirect(url_for("dev.notify_rules"))
    info = PLACEHOLDERS.get(slug)
    if not info:
        from flask import abort
        abort(404)
    title_en, title_ar, body_en, body_ar, backend = info
    lang = i18n.get_lang()
    return render_template(
        "placeholder.html",
        title=title_en if lang == "en" else title_ar,
        body=body_en if lang == "en" else body_ar,
        backend=backend,
        soon="Coming soon" if lang == "en" else "قريبًا")
