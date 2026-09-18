"""Login/logout/site/preferences (ticket-037)."""
from __future__ import annotations

from flask import (Blueprint, current_app, make_response, redirect,
                   render_template, request, session, url_for)

from app.web import auth as webauth
from app.web import authz
from app.web import i18n

bp = Blueprint("auth", __name__)


def _db():
    return current_app.extensions["atlas_db"]


@bp.get("/login")
def login():
    if authz.current_chat_id():
        return redirect(url_for("shell.home"))
    return render_template("login.html",
                           csrf_open=True, next=request.args.get("next", "/"))


@bp.post("/login")
def login_post():
    db = _db()
    user = webauth.verify_password(
        db, request.form.get("username", ""), request.form.get("password", ""))
    if not user:
        return render_template("login.html", csrf_open=True,
                               error=i18n.t("sign_in_failed"),
                               next=request.form.get("next", "/")), 401
    # Bridge check: linked Atlas user row must exist and not be rejected.
    authsvc = current_app.extensions["atlas_auth"]
    row = authsvc.get_user(user["chat_id"])
    if row is None or getattr(row, "role", "") == "rejected":
        return render_template("login.html", csrf_open=True,
                               error=i18n.t("sign_in_failed"),
                               next=request.form.get("next", "/")), 401
    sid, csrf = webauth.create_session(db, user["chat_id"])
    session.clear()
    session["sid"] = sid
    session["chat_id"] = user["chat_id"]
    session["web_user"] = user["username"]
    session["csrf_token"] = csrf
    session["active_site"] = None  # resolved lazily via resolve_site()
    resp = make_response(redirect(request.form.get("next") or
                                  url_for("shell.home")))
    resp.set_cookie(webauth.SESSION_COOKIE, sid, httponly=True,
                    samesite="Lax")
    return resp


@bp.post("/logout")
@authz.csrf_protect
def logout():
    webauth.destroy_session(_db(),
                            request.cookies.get(webauth.SESSION_COOKIE))
    session.clear()
    resp = make_response(redirect(url_for("auth.login")))
    resp.delete_cookie(webauth.SESSION_COOKIE)
    return resp


@bp.post("/site")
@authz.login_required
@authz.csrf_protect
def switch_site():
    authz.set_active_site(request.form.get("site_id", ""))
    return redirect(request.form.get("next") or url_for("shell.home"))


@bp.post("/prefs")
def prefs():
    """Display-only preferences (language/theme). Never auth state."""
    resp = make_response(redirect(request.form.get("next") or "/"))
    lang = request.form.get("lang", "")
    theme = request.form.get("theme", "")
    if lang in i18n.LANGS:
        resp.set_cookie("atlas_lang", lang, samesite="Lax")
    if theme in ("light", "dark", "system"):
        resp.set_cookie("atlas_theme", theme, samesite="Lax")
    return resp
