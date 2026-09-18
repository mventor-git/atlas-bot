"""DEV / Configuration surface (ticket-037).

Read-first: every page exposes what the backend already models. Writes
exist ONLY where a safe backend operation exists (membership grant/
suspend/revoke, pending-user approve/reject) and are SUPERADMIN-gated,
mirroring /hr_member. Payroll policy stays read-only (business-gated).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from app.web import authz

bp = Blueprint("dev", __name__)
ROOT = Path(__file__).parent.parent.parent


def _svc():
    return current_app.extensions["atlas_services"]


def _auth():
    return current_app.extensions["atlas_auth"]


def _site_or_403():
    site = authz.resolve_site()
    if not site:
        abort(403)
    return site


@bp.get("/config")
@authz.login_required
def config_home():
    return render_template("dev_home.html", active="config")


# --- sites -----------------------------------------------------------------

@bp.get("/config/sites")
@authz.login_required
def sites():
    s = _svc()
    cfg = s["config"]
    rows = []
    for entry in (cfg.sites or []):
        sid = entry.get("id") if isinstance(entry, dict) else str(entry)
        try:
            members = len(s["membership_repo"].get_all(sid))
        except Exception:
            members = None
        try:
            pending = s["outbox"].pending_count(sid)
        except Exception:
            pending = None
        rows.append({"id": sid, "members": members,
                     "outbox_pending": pending, "raw": entry})
    return render_template("dev_sites.html", active="config", rows=rows)


# --- users -----------------------------------------------------------------

@bp.get("/config/users")
@authz.login_required
def users():
    s = _svc()
    f = request.args.get("f", "")
    rows = s["user_repo"].get_all_users()
    if f == "pending":
        rows = [u for u in rows if u.role == "pending"]
    is_sa = _auth().is_super_admin(authz.current_chat_id())
    return render_template("dev_users.html", active="config", rows=rows,
                           f=f, is_sa=is_sa)


@bp.post("/config/users/<chat>/approve")
@authz.require_superadmin
@authz.csrf_protect
def user_approve(chat: str):
    try:
        _auth().approve_user(chat, authz.current_chat_id())
        flash(f"Approved {chat}.")
    except Exception as e:
        flash(f"Could not approve: {e}")
    return redirect(url_for("dev.users", f="pending"))


@bp.post("/config/users/<chat>/reject")
@authz.require_superadmin
@authz.csrf_protect
def user_reject(chat: str):
    try:
        _auth().reject_user(chat, authz.current_chat_id())
        flash(f"Rejected {chat}.")
    except Exception as e:
        flash(f"Could not reject: {e}")
    return redirect(url_for("dev.users", f="pending"))


# --- memberships ------------------------------------------------------------

@bp.get("/config/memberships")
@authz.login_required
def memberships():
    site = _site_or_403()
    s = _svc()
    rows = s["membership_repo"].get_all(site)
    is_sa = _auth().is_super_admin(authz.current_chat_id())
    return render_template("dev_memberships.html", active="config",
                           site=site, rows=rows, is_sa=is_sa)


@bp.post("/config/memberships/grant")
@authz.require_superadmin
@authz.csrf_protect
def membership_grant():
    from app.auth.capabilities import is_known

    chat_id = request.form.get("chat_id", "").strip()
    site = request.form.get("site_id", "").strip()
    caps = [c.strip() for c in request.form.get("capabilities", "").split(",")
            if c.strip()]
    bad = [c for c in caps if not is_known(c)]
    if not chat_id or not site:
        flash("chat_id and site_id are required.")
    elif bad:
        flash(f"Unknown capabilities refused: {', '.join(bad)}")
    else:
        try:
            _auth().grant_membership(chat_id, site, caps or None)
            flash(f"Granted {chat_id} @ {site}.")
        except Exception as e:
            flash(f"Could not grant: {e}")
    return redirect(url_for("dev.memberships"))


@bp.post("/config/memberships/suspend")
@authz.require_superadmin
@authz.csrf_protect
def membership_suspend():
    try:
        ok = _auth().suspend_membership(request.form.get("chat_id", ""),
                                        request.form.get("site_id", ""))
        flash("Suspended." if ok else "No such membership.")
    except Exception as e:
        flash(f"Could not suspend: {e}")
    return redirect(url_for("dev.memberships"))


@bp.post("/config/memberships/revoke")
@authz.require_superadmin
@authz.csrf_protect
def membership_revoke():
    try:
        ok = _auth().revoke_membership(request.form.get("chat_id", ""),
                                       request.form.get("site_id", ""))
        flash("Revoked." if ok else "No such membership.")
    except Exception as e:
        flash(f"Could not revoke: {e}")
    return redirect(url_for("dev.memberships"))


# --- capabilities -----------------------------------------------------------

@bp.get("/config/capabilities")
@authz.login_required
def capabilities():
    from app.auth import capabilities as caps

    rows = [{"name": k, "scope": v[0], "desc": v[1]}
            for k, v in sorted(caps.CAPABILITIES.items())]
    roles = {r: sorted(list(v)) for r, v in caps.ROLE_DEFAULTS.items()}
    return render_template("dev_capabilities.html", active="config",
                           rows=rows, roles=roles, scopes=caps.SCOPES)


# --- working calendar -------------------------------------------------------

@bp.get("/config/calendar")
@authz.login_required
def calendar():
    from app.services.holiday_calendar import HolidayCalendarService
    from app.services.working_calendar import WorkingCalendar

    site = _site_or_403()
    s = _svc()
    cal = WorkingCalendar(s["config"], site)
    holy = HolidayCalendarService(s["config"])
    today = date.today()
    upcoming = []
    try:
        for h in holy.get_holidays_in_range(today.isoformat(),
                                            date(today.year, 12, 31).isoformat()):
            upcoming.append(h)
    except Exception:
        upcoming = None
    site_cfg = cal._site() if hasattr(cal, "_site") else {}
    return render_template(
        "dev_calendar.html", active="config", site=site,
        weekend=cal.weekend_days(), upcoming=upcoming, site_cfg=site_cfg,
        today=today.isoformat(),
        probe={d: cal.describe(d) for d in
               [today.isoformat()]},
    )


# --- templates ---------------------------------------------------------------

@bp.get("/config/templates")
@authz.login_required
def templates():
    s = _svc()
    cfg = s["config"]
    paths = [cfg.template.small_template, cfg.template.medium_template,
             cfg.template.large_template]
    is_sa = _auth().is_super_admin(authz.current_chat_id())
    return render_template("dev_templates.html", active="config",
                           paths=paths, is_sa=is_sa, result=None)


@bp.post("/config/templates/validate")
@authz.require_superadmin
@authz.csrf_protect
def templates_validate():
    from app.libre.validate import TemplateValidator

    s = _svc()
    cfg = s["config"]
    v = TemplateValidator(cfg)
    out = []
    for p in (cfg.template.small_template, cfg.template.medium_template,
              cfg.template.large_template):
        try:
            rep = v.check_template(p)
            out.append({"file": str(p), "passed": rep.passed,
                        "failures": [str(f) for f in rep.failures]})
        except Exception as e:
            out.append({"file": str(p), "passed": False,
                        "failures": [str(e)]})
    return render_template("dev_templates.html", active="config",
                           paths=[r["file"] for r in out],
                           is_sa=True, result=out)


# --- notification rules (read-only config) ------------------------------------

@bp.get("/config/notifications")
@authz.login_required
def notify_rules():
    s = _svc()
    cfg = s["config"]
    try:
        rules = vars(cfg.notification)
    except Exception:
        rules = {}
    try:
        pending = s["outbox"].pending_count(authz.resolve_site())
    except Exception:
        pending = None
    return render_template("dev_notify.html", active="config", rules=rules,
                           pending=pending)


# --- payroll policy (read-only) -------------------------------------------------

@bp.get("/config/payroll-policy")
@authz.login_required
def payroll_policy():
    site = _site_or_403()
    s = _svc()
    try:
        history = s["payroll_repo"].policies_for(site)
    except Exception:
        history = []
    try:
        active = s["payroll"].active_policy(site, date.today().strftime("%Y-%m"))
    except Exception:
        active = None
    return render_template("dev_policy.html", active="config", site=site,
                           history=history, policy_active=active)


# --- system health (real checks only) --------------------------------------------

@bp.get("/config/health")
@authz.login_required
def health():
    from app.libre.validate import TemplateValidator

    s = _svc()
    cfg = s["config"]
    site = authz.resolve_site()
    checks = []
    checks.append(("Config loaded", True, str(cfg)))
    try:
        s["db"].execute("SELECT 1").fetchone()
        checks.append(("Database connect", True, str(cfg.database_path)))
    except Exception as e:
        checks.append(("Database connect", False, str(e)[:120]))
    try:
        n = s["outbox"].pending_count(site)
        checks.append(("Outbox pending", True, f"{n} pending @ {site}"))
    except Exception as e:
        checks.append(("Outbox pending", False, str(e)[:120]))
    try:
        reps = [TemplateValidator(cfg).check_template(p) for p in
                (cfg.template.small_template, cfg.template.medium_template,
                 cfg.template.large_template)]
        ok = all(r.passed for r in reps)
        checks.append(("Templates valid", ok,
                       ", ".join(f"{r.file}: {'PASS' if r.passed else 'FAIL'}"
                                 for r in reps)))
    except Exception as e:
        checks.append(("Templates valid", False, str(e)[:120]))
    bdir = ROOT / "backups"
    snaps = sorted(bdir.glob("atlas-*.db*")) if bdir.exists() else []
    drill = (bdir / "RESTORE_DRILL_OK").exists()
    if snaps:
        from datetime import datetime
        age_h = (datetime.now().timestamp()
                 - snaps[-1].stat().st_mtime) / 3600
        checks.append(("Backups", age_h < 30 and drill,
                       f"newest {age_h:.0f}h old, drill={drill}"))
    else:
        checks.append(("Backups", False, "no snapshots yet"))
    checks.append(("Scheduler", None, "bot process — not observable from web"))
    return render_template("dev_health.html", active="config", checks=checks)


# --- audit viewer (read-only) -----------------------------------------------------

@bp.get("/audit")
@authz.login_required
def audit_view():
    site = _site_or_403()
    s = _svc()
    f_actor = request.args.get("actor", "").strip()
    f_action = request.args.get("action", "").strip()
    try:
        if f_action:
            rows = s["events"].get_by_action(f_action, limit=100) or []
        elif f_actor:
            rows = s["events"].get_by_user(f_actor, limit=100) or []
        else:
            rows = s["events"].get_recent(limit=100) or []
    except Exception:
        rows = []
    rows = [e for e in rows
            if (getattr(e, "site_id", None) or site) == site][:100]
    try:
        activity = s["audit"].get_recent_activity(limit=50) or []
    except Exception:
        activity = []
    activity = [a for a in activity
                if (getattr(a, "site_id", None) or site) == site][:50]
    return render_template("dev_audit.html", active="audit", site=site,
                           rows=rows, activity=activity,
                           f_actor=f_actor, f_action=f_action)
