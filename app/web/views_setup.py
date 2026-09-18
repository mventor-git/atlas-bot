"""Fresh-start setup wizard (ticket-037).

Ordered journey borrowed from ecom-erp SetupWizard, Atlas rules underneath:
1. start the site (active-site pick, server-validated), 2. read the manual
(ack stored server-side per web login), 3. work the server-derived
checklist. Guidance only — free navigation always remains.
"""
from __future__ import annotations

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

from app.web import authz
from app.web.setup import get_manual_ack, set_manual_ack, setup_status

bp = Blueprint("setup", __name__)


def _ctx():
    s = current_app.extensions["atlas_services"]
    chat_id = authz.current_chat_id()
    ack = get_manual_ack(s["db"], chat_id)
    return s, chat_id, ack, setup_status(s, chat_id, ack)


@bp.get("/setup")
@authz.login_required
def wizard():
    s, chat_id, ack, status = _ctx()
    return render_template("setup.html", active="setup",
                           status=status, ack=ack, sites=authz.g.sites,
                           current=session.get("active_site"))


@bp.post("/setup/ack")
@authz.login_required
@authz.csrf_protect
def ack():
    s, chat_id, _ack, _status = _ctx()
    set_manual_ack(s["db"], chat_id, request.form.get("ack") == "1")
    flash("Noted.")
    return redirect(url_for("setup.wizard"))
