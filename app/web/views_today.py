"""Today-board (WEB_HQ_SPEC BUILD-1): contractor rows + finalize + v3 preview.

Reuses the bot's seams: ReportRepository (shared tables), ReportWorkflowService
(same DRAFT->FINAL path), daily_v3.build + filler.details_text for preview,
EventLogService for the off-hours audit trail. Bot handlers untouched.
"""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, send_file, session, url_for)

from app.models.database import Report, ReportItem, ReportStatus
from app.services.arabic_date_service import ArabicDateService
from app.utils.business_hours import is_business_hours
from app.web import authz

bp = Blueprint("today", __name__)

OVERRIDE_CAP = "override_offhours"


def _svc():
    return current_app.extensions["atlas_services"]


def _auth():
    return current_app.extensions["atlas_auth"]


def _site_or_403():
    site = authz.resolve_site()
    if not site:
        abort(403)
    return site


def _can(chat_id, cap, site) -> bool:
    return _auth().has_capability(chat_id, cap, site)


def _valid_date(value: str | None) -> str:
    try:
        date.fromisoformat(value or "")
        return value  # type: ignore[return-value]
    except ValueError:
        return date.today().isoformat()


def _override_key(site: str, day: str) -> str:
    return f"offhours_ok:{site}:{day}"


def _int_or_none(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@bp.get("/today")
@authz.login_required
@authz.require_capability("view_site_reports")
def board():
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    day = _valid_date(request.args.get("date"))
    report = s["reports"].get_by_date(day, site_id=site)
    try:
        att_queue = s["attendance_days"].queue(day, site)
    except Exception:
        att_queue = None  # honest unknown, not zero
    return render_template(
        "today_board.html", active="today", site=site, day=day,
        report=report, items=(report.items if report else []),
        att_queue=att_queue,
        off_hours=not is_business_hours(),
        override_ok=bool(session.get(_override_key(site, day))),
        can_edit=_can(chat_id, "create_daily_report", site),
        can_finalize=_can(chat_id, "approve_daily_report", site),
        can_override=_can(chat_id, OVERRIDE_CAP, site))


@bp.post("/today/<day>/rows")
@authz.login_required
@authz.require_capability("create_daily_report")
@authz.csrf_protect
def save_row(day: str):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    day = _valid_date(day)
    report = s["reports"].get_by_date(day, site_id=site)
    if report is None:
        report = Report(
            date=day,
            day=ArabicDateService.get_day_name(date.fromisoformat(day)),
            status=ReportStatus.DRAFT, telegram_user=chat_id, site_id=site)
        report = s["reports"].add(report)
    if report.status != ReportStatus.DRAFT:
        flash("Only draft reports can be edited.")
        return redirect(url_for("today.board", date=day))
    item_id = _int_or_none(request.form.get("item_id", ""))
    workers = _int_or_none(request.form.get("workers", ""))
    craftsmen = _int_or_none(request.form.get("craftsmen", ""))
    helpers = _int_or_none(request.form.get("helpers", ""))
    fields = {
        "contractor": request.form.get("contractor", "").strip(),
        "type": request.form.get("type", "").strip() or None,
        "zone": request.form.get("zone", "").strip() or None,
        "workers": workers, "craftsmen": craftsmen, "helpers": helpers,
        "details": request.form.get("details", "").strip() or None,
    }
    if not fields["contractor"]:
        flash("Contractor name is required.")
        return redirect(url_for("today.board", date=day))
    if item_id is None:
        report.items.append(ReportItem(**fields))
    else:
        target = next((i for i in report.items if i.id == item_id), None)
        if target is None:
            abort(404)
        for k, v in fields.items():
            setattr(target, k, v)
    s["reports"].update(report, force=True)
    flash("Row saved.")
    return redirect(url_for("today.board", date=day))


@bp.post("/today/<day>/finalize")
@authz.login_required
@authz.require_capability("approve_daily_report")
@authz.csrf_protect
def finalize(day: str):
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    day = _valid_date(day)
    report = s["reports"].get_by_date(day, site_id=site)
    if report is None:
        flash("Nothing to finalize for this date.")
        return redirect(url_for("today.board", date=day))
    if not is_business_hours() and not session.get(
            _override_key(site, day)):
        flash("Outside 8-17: confirm the off-hours override with a reason first.")
        return redirect(url_for("today.board", date=day))
    try:
        s["workflow"].finalize_report(report, chat_id or "web")
        session.pop(_override_key(site, day), None)
        flash("Finalized.")
    except Exception as e:
        flash(f"Could not finalize: {e}")
    return redirect(url_for("today.board", date=day))


@bp.get("/today/<day>/v3-preview")
@authz.login_required
@authz.require_capability("view_site_reports")
def v3_preview(day: str):
    site = _site_or_403()
    s = _svc()
    day = _valid_date(day)
    report = s["reports"].get_by_date(day, site_id=site)
    if report is None:
        abort(404)
    from app.libre.daily_v3 import build as build_v3
    tmp = Path(tempfile.mkdtemp(prefix="atlas_v3_")) / f"{day}.ods"
    try:
        # ponytail: existing build path only; no soffice convert in the request.
        out = build_v3(report, s["config"], output_path=str(tmp))
    except Exception as e:
        abort(500, description=f"Preview build failed: {e}")
    download = request.args.get("download") == "1"
    return send_file(
        out,
        mimetype="application/vnd.oasis.opendocument.spreadsheet",
        as_attachment=download,
        download_name=f"daily-v3-{day}.ods")


@bp.post("/today/<day>/override")
@authz.login_required
@authz.require_capability(OVERRIDE_CAP)
@authz.csrf_protect
def override(day: str):
    """Off-hours Proceed (bot mirror): reason required, audit-logged, labeled.

    Cancel = the board's Back link (clears the flag, no changes made).
    """
    site = _site_or_403()
    s = _svc()
    chat_id = authz.current_chat_id()
    day = _valid_date(day)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A reason is required for off-hours work (override rejected).")
        return redirect(url_for("today.board", date=day))
    report = s["reports"].get_by_date(day, site_id=site)
    s["events"].log(
        chat_id or "web", "offhours.override", object_type="report",
        object_id=(report.id if report else None), object_date=day,
        new_value=f"(off-hours) {reason}")
    session[_override_key(site, day)] = True
    flash("Off-hours confirmed. Proceed (labeled (off-hours)).")
    return redirect(url_for("today.board", date=day))


@bp.post("/today/<day>/override-cancel")
@authz.login_required
@authz.csrf_protect
def override_cancel(day: str):
    """Off-hours Cancel (bot mirror): clears the flag, no changes made."""
    site = _site_or_403()
    session.pop(_override_key(site, _valid_date(day)), None)
    flash("Cancelled - no changes made.")
    return redirect(url_for("today.board", date=day))
