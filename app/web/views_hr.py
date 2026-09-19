"""HR web module (ticket-037): real queue/detail/decisions on HRService.

- Dashboard counts come from service queries; empty states say "No data yet".
- Approve/reject/confirm call the domain service: SoD + chain order are
  enforced there, never in the template. UI buttons are convenience only.
- "Request clarification" has no domain transition: reject-with-note is the
  channel (the note reaches the requester as the clarification ask).
- Warnings have no backend domain yet: /warnings shows open DISCIPLINE
  cases explicitly labeled as such, never as warnings.
"""
from __future__ import annotations

from datetime import date

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from app.web import authz

bp = Blueprint("hr", __name__)


def _svc():
    return current_app.extensions["atlas_services"]


def _auth():
    return current_app.extensions["atlas_auth"]


def _site_or_403():
    site = authz.resolve_site()
    if not site:
        abort(403)
    return site


def _can(chat_id, cap, site):
    return _auth().has_capability(chat_id, cap, site)


def _mask(chat_id: str | None) -> str:
    """P0: never render raw chat IDs; show a masked suffix only."""
    s = str(chat_id or "")
    return f"***{s[-4:]}" if len(s) > 4 else "***"


def _audit(action: str, chat_id: str, req_id: int, site: str,
           new_value: str = "") -> None:
    """Append web decisions to the existing event-log trail (bot shares it).

    Identity column keeps the chat_id (storage join); the human-readable
    value carries ``CODE · Name`` so audit never shows raw chat IDs.
    """
    try:
        by = _who(chat_id)
        value = f"{new_value} · by {by}".strip(" ·") if new_value else f"by {by}"
        _svc()["events"].log(
            chat_id or "web", action, object_type="hr_request",
            object_id=req_id, new_value=value or None)
    except Exception:
        pass  # audit never breaks the decision itself


def _directory():
    """Employee directory repo (None when unwired)."""
    try:
        return _svc()["employees"]
    except Exception:
        return None


def _who(chat_id: str | None) -> str:
    """Card identity: ``CODE · DB-name``; never Telegram name/chat digits."""
    repo = _directory()
    if repo is None:
        return "UNMAPPED · limited"
    return repo.display(chat_id or "")


def _actor_name(chat_id: str, fallback: str) -> str:
    """Stored actor name: DB full_name when mapped, else web identity."""
    repo = _directory()
    if repo is None:
        return fallback
    code, name = repo.resolve(chat_id)
    return name if code != "UNMAPPED" else fallback


@bp.get("/hr")
@authz.login_required
def dashboard():
    site = _site_or_403()
    s = _svc()
    pending = s["hr"].pending(site)
    reviews = [r for r in pending if r.status == "pm_confirmed"]
    grievances = s["cases"].open_cases(site)
    discipline = s["discipline"].open_cases(site)
    try:
        att_queue = s["attendance_days"].queue(date.today().isoformat(), site)
    except Exception:
        att_queue = None  # honest unknown, not zero
    try:
        runs = s["payroll"].list_runs(site)
    except Exception:
        runs = None
    recent = sorted(grievances,
                    key=lambda c: c.created_at or "", reverse=True)[:5]
    return render_template(
        "hr_dashboard.html", active="hr", site=site,
        pending=pending, reviews=reviews, grievances=grievances,
        discipline=discipline, att_queue=att_queue, runs=runs,
        recent=recent)


@bp.get("/hr/requests")
@authz.login_required
@authz.require_capability(("decide_hr_request", "confirm_hr_request"))
def queue():
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    f_type = request.args.get("type", "")
    f_status = request.args.get("status", "")
    rows = s["hr"].queue(site)  # LOW last, never dropped
    if f_type:
        rows = [r for r in rows if r.request_type == f_type]
    if f_status:
        rows = [r for r in rows if r.status == f_status]
    try:
        hr_targets = _auth().chat_ids_for_site(site, "decide_hr_request")
    except Exception:
        hr_targets = []
    who = {c: _who(c) for c in
           ({r.requester_chat_id for r in rows} | set(hr_targets))}
    prio = {r.id: s["hr"].priority(r) for r in rows}
    return render_template(
        "hr_queue.html", active="hr", site=site, rows=rows,
        f_type=f_type, f_status=f_status, mask=_mask, who=who, prio=prio,
        hr_targets=hr_targets,
        can_confirm=_can(chat_id, "confirm_hr_request", site),
        can_delegate=_can(chat_id, "delegate_hr_request", site),
        can_decide=_can(chat_id, "decide_hr_request", site))


@bp.get("/hr/requests/<int:req_id>")
@authz.login_required
@authz.require_capability(("decide_hr_request", "confirm_hr_request"))
def detail(req_id: int):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    req = s["hr"].get(req_id, site)
    if req is None or (req.site_id or site) != site:
        abort(404)
    requester = _auth().get_user(req.requester_chat_id)
    try:
        money = s["hr"].financial_status(req_id, site)
    except Exception:
        money = None
    try:
        hr_targets = _auth().chat_ids_for_site(site, "decide_hr_request")
    except Exception:
        hr_targets = []
    who_req = _who(req.requester_chat_id)
    who = {h: _who(h) for h in hr_targets}
    prio_req = s["hr"].priority(req)
    return render_template(
        "hr_detail.html", active="hr", site=site, req=req,
        requester=requester, money=money, mask=_mask,
        who_req=who_req, who=who, prio_req=prio_req,
        hr_targets=hr_targets,
        can_confirm=_can(chat_id, "confirm_hr_request", site),
        can_delegate=_can(chat_id, "delegate_hr_request", site),
        can_decide=_can(chat_id, "decide_hr_request", site),
        can_payout=_can(chat_id, "confirm_payout", site),
        can_deduct=_can(chat_id, "confirm_payroll_deduction", site))


@bp.post("/hr/requests/<int:req_id>/confirm")
@authz.login_required
@authz.require_capability("confirm_hr_request")
@authz.csrf_protect
def confirm(req_id: int):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    me = _auth().get_user(chat_id)
    name = _actor_name(chat_id, (me.first_name or me.username or chat_id) if me else chat_id)
    try:
        s["hr"].confirm_pm(req_id, chat_id, name, site)
        _audit("hr.confirm", chat_id, req_id, site, "pending->pm_confirmed")
        flash("Confirmed. Moved to HR decision.")
    except Exception as e:
        flash(f"Could not confirm: {e}")
    return redirect(url_for("hr.detail", req_id=req_id))


@bp.post("/hr/requests/<int:req_id>/delegate")
@authz.login_required
@authz.require_capability("delegate_hr_request")
@authz.csrf_protect
def delegate(req_id: int):
    """PM delegate-to-HR (bot parity: single hop, target must decide)."""
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    me = _auth().get_user(chat_id)
    name = _actor_name(chat_id, (me.first_name or me.username or chat_id) if me else chat_id)
    target = request.form.get("target", "").strip()
    if not target:
        flash("Pick the HQ HR account to delegate to.")
        return redirect(url_for("hr.detail", req_id=req_id))
    try:
        req = s["hr"].get(req_id, site)
        req_site = (req.site_id or site) if req else site
        if not _auth().has_capability(target, "decide_hr_request", req_site):
            flash("Target has no HR decision rights at this site.")
            return redirect(url_for("hr.detail", req_id=req_id))
        s["hr"].delegate_to_hr(req_id, chat_id, name, target, site_id=site)
        _audit("hr.delegate", chat_id, req_id, site, f"delegated to {_who(target)}")
        flash("Delegated to HR.")
    except Exception as e:
        flash(f"Could not delegate: {e}")
    return redirect(url_for("hr.detail", req_id=req_id))


@bp.post("/hr/requests/<int:req_id>/decide")
@authz.login_required
@authz.require_capability("decide_hr_request")
@authz.csrf_protect
def decide(req_id: int):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    me = _auth().get_user(chat_id)
    name = _actor_name(chat_id, (me.first_name or me.username or chat_id) if me else chat_id)
    approve = request.form.get("decision") == "approve"
    note = request.form.get("note", "").strip()
    month = request.form.get("deduction_month", "").strip()
    if not approve and not note:
        flash("A note is required to reject (it is the clarification ask).")
        return redirect(url_for("hr.detail", req_id=req_id))
    try:
        s["hr"].decide_hr(req_id, chat_id, name, approve,
                          note=note, deduction_month=month, site_id=site)
        _audit("hr.decide", chat_id, req_id, site,
               f"{'approved' if approve else 'rejected'} {note or month}".strip())
        flash("Approved." if approve else "Rejected with note.")
    except Exception as e:
        flash(f"Could not decide: {e}")
    return redirect(url_for("hr.detail", req_id=req_id))


@bp.post("/hr/requests/<int:req_id>/payout")
@authz.login_required
@authz.require_capability("confirm_payout")
@authz.csrf_protect
def payout(req_id: int):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    try:
        amount = float(request.form.get("amount", "0"))
    except (TypeError, ValueError):
        flash("Amount must be a number.")
        return redirect(url_for("hr.detail", req_id=req_id))
    try:
        s["hr"].record_payout(
            req_id, amount, request.form.get("payout_date", "").strip(),
            chat_id, reference=request.form.get("reference", "").strip(),
            note=request.form.get("note", "").strip(), site_id=site)
        _audit("hr.payout", chat_id, req_id, site, f"paid {amount}")
        flash("Payout recorded.")
    except Exception as e:
        flash(f"Could not record payout: {e}")
    return redirect(url_for("hr.detail", req_id=req_id))


@bp.post("/hr/requests/<int:req_id>/deduction")
@authz.login_required
@authz.require_capability("confirm_payroll_deduction")
@authz.csrf_protect
def deduction(req_id: int):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    try:
        amount = float(request.form.get("amount", "0"))
    except (TypeError, ValueError):
        flash("Amount must be a number.")
        return redirect(url_for("hr.detail", req_id=req_id))
    try:
        s["hr"].record_deduction(
            req_id, amount, request.form.get("period", "").strip(),
            chat_id, deduction_date=request.form.get(
                "deduction_date", "").strip(),
            reference=request.form.get("reference", "").strip(),
            note=request.form.get("note", "").strip(), site_id=site)
        _audit("hr.deduction", chat_id, req_id, site,
               f"deducted {amount} {request.form.get('period', '')}".strip())
        flash("Deduction recorded.")
    except Exception as e:
        flash(f"Could not record deduction: {e}")
    return redirect(url_for("hr.detail", req_id=req_id))


# --- attendance claims (existing model, newly wired to web) -------------------

@bp.get("/hr/claims")
@authz.login_required
@authz.require_capability(("manage_attendance", "manage_payroll"))
def claims():
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    try:
        rows = s["attendance_days"].open_claims(site_id=site)
    except Exception:
        rows = []
    return render_template(
        "hr_claims.html", active="hr", site=site, rows=rows, mask=_mask,
        can_decide=_can(chat_id, "manage_attendance", site))


@bp.post("/hr/claims/<int:claim_id>/decide")
@authz.login_required
@authz.require_capability("manage_attendance")
@authz.csrf_protect
def claim_decide(claim_id: int):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    approve = request.form.get("decision") == "approve"
    note = request.form.get("note", "").strip()
    if not note:
        flash("Claim decisions require a note.")
        return redirect(url_for("hr.claims"))
    try:
        s["attendance_days"].decide_claim(
            claim_id, chat_id, approve, note, site_id=site)
        _audit("attendance.claim_decide", chat_id, claim_id, site,
               f"{'approved' if approve else 'denied'}: {note}")
        flash("Claim approved." if approve else "Claim denied.")
    except Exception as e:
        flash(f"Could not decide claim: {e}")
    return redirect(url_for("hr.claims"))


# --- employees -------------------------------------------------------------

def _directory_allowed(chat_id, site) -> bool:
    auth = _auth()
    return (auth.is_admin(chat_id)
            or _can(chat_id, "decide_hr_request", site)
            or _can(chat_id, "manage_payroll", site))


@bp.get("/hr/employees")
@authz.login_required
def employees():
    site = _site_or_403()
    chat_id = authz.current_chat_id()
    if not _directory_allowed(chat_id, site):
        abort(403)
    s = _svc()
    members = s["membership_repo"].get_all(site)
    by_chat = {m.chat_id: m for m in members}
    users = [u for u in s["user_repo"].get_all_users()
             if u.chat_id in by_chat]
    q = request.args.get("q", "").strip().lower()
    f_status = request.args.get("status", "")
    if q:
        users = [u for u in users
                 if q in (u.first_name or "").lower()
                 or q in (u.username or "").lower()
                 or q in u.chat_id.lower()]
    if f_status:
        users = [u for u in users if u.role == f_status]
    show_salary = _can(chat_id, "manage_payroll", site)
    return render_template("hr_employees.html", active="employees", site=site,
                           users=users, memberships=by_chat, q=q,
                           f_status=f_status, show_salary=show_salary)


@bp.get("/hr/employees/<chat>")
@authz.login_required
def employee_detail(chat: str):
    site = _site_or_403()
    me = authz.current_chat_id()
    if not _directory_allowed(me, site):
        abort(403)
    s = _svc()
    user = s["user_repo"].get_by_chat_id(chat)
    mem = s["membership_repo"].find(chat, site)
    if user is None or mem is None:
        abort(404)
    show_salary = _can(me, "manage_payroll", site)
    all_memberships = s["membership_repo"].active_for_user(chat)
    return render_template("hr_employee.html", active="employees", site=site,
                           user=user, mem=mem,
                           all_memberships=all_memberships,
                           show_salary=show_salary)


# --- grievances ------------------------------------------------------------

@bp.get("/hr/grievances")
@authz.login_required
def grievances():
    site = _site_or_403()
    s = _svc()
    status = request.args.get("status", "")
    try:
        rows = s["case_repo"].get_all(site)
    except Exception:
        rows = s["cases"].open_cases(site)
    buckets = {"filed": [], "under_review": [], "resolved": [],
               "appealed": []}
    for c in rows:
        buckets.setdefault(c.status, []).append(c)
    if status and status in buckets:
        rows = buckets[status]
    counts = {k: len(v) for k, v in buckets.items()}
    return render_template("hr_grievances.html", active="grievances",
                           site=site, rows=rows, status=status,
                           counts=counts)


@bp.get("/hr/grievances/<int:case_id>")
@authz.login_required
def grievance_detail(case_id: int):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    case = s["cases"].get(case_id, site)
    if case is None or (case.site_id or site) != site:
        abort(404)
    reporter = _auth().get_user(case.reporter_chat_id)
    return render_template(
        "hr_grievance.html", active="grievances", site=site, case=case,
        reporter=reporter,
        can_review=_can(chat_id, "review_grievance", site),
        can_resolve=_can(chat_id, "resolve_grievance", site))


@bp.post("/hr/grievances/<int:case_id>/review")
@authz.login_required
@authz.require_capability("review_grievance")
@authz.csrf_protect
def grievance_review(case_id: int):
    site = _site_or_403()
    try:
        _svc()["cases"].review(case_id, authz.current_chat_id(), site)
        flash("Under review.")
    except Exception as e:
        flash(f"Could not review: {e}")
    return redirect(url_for("hr.grievance_detail", case_id=case_id))


@bp.post("/hr/grievances/<int:case_id>/resolve")
@authz.login_required
@authz.require_capability("resolve_grievance")
@authz.csrf_protect
def grievance_resolve(case_id: int):
    site = _site_or_403()
    note = request.form.get("note", "").strip()
    if not note:
        flash("A resolution note is required.")
        return redirect(url_for("hr.grievance_detail", case_id=case_id))
    try:
        _svc()["cases"].resolve(case_id, authz.current_chat_id(), note, site)
        flash("Resolved.")
    except Exception as e:
        flash(f"Could not resolve: {e}")
    return redirect(url_for("hr.grievance_detail", case_id=case_id))


# --- warnings (no backend domain: discipline cases, labeled honestly) ------

@bp.get("/hr/warnings")
@authz.login_required
def warnings():
    site = _site_or_403()
    s = _svc()
    rows = s["discipline"].open_cases(site)
    filed = [c for c in rows if c.status == "filed"]
    decided = [c for c in rows if c.status not in ("filed",)]
    return render_template("hr_warnings.html", active="warnings", site=site,
                           filed=filed, decided=decided)
