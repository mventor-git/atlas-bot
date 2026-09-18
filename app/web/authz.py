"""Web authorization guards (ticket-037). Thin wrappers, zero new rules.

Every guard resolves the Atlas subject (chat_id) from the server session,
then calls the EXISTING AuthorizationService. Browser values (site_id,
user_id, role, capability) are treated as untrusted input: the requested
site is always revalidated against sites_for_user(); capabilities are
always checked server-side via has_capability().
"""
from __future__ import annotations

from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for

from app.web import auth as webauth


def _db():
    return current_app.extensions["atlas_db"]


def _authsvc():
    return current_app.extensions["atlas_auth"]


def current_chat_id() -> str | None:
    return session.get("chat_id")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_chat_id():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def load_identity():
    """Resolve chat_id -> Atlas user + memberships for the request.

    Re-checks the web login row every request: a disabled or deleted
    login loses access immediately even with a live session cookie.
    """
    from flask import session as _session

    g.chat_id = _session.get("chat_id")
    g.atlas_user = None
    g.sites = []
    if g.chat_id:
        svc = current_app.extensions["atlas_auth"]
        # Server-side session is authoritative: a deleted/expired row
        # (logout elsewhere, TTL) kills the login even with a valid cookie.
        if not webauth.get_session(_db(), _session.get("sid")):
            _session.clear()
            g.chat_id = None
            return
        row = webauth.get_web_user_by_chat(_db(), g.chat_id)
        if not row or row.get("disabled"):
            _session.clear()
            g.chat_id = None
            return
        g.atlas_user = svc.get_user(g.chat_id)
        g.sites = svc.sites_for_user(g.chat_id)


def require_capability(capability: str | tuple | list):
    """Route guard: caller must hold capability (or any of a tuple/list)
    on the resolved site. Server-side only; UI hiding is never enough."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            chat_id = current_chat_id()
            if not chat_id:
                return redirect(url_for("auth.login", next=request.path))
            site = resolve_site()
            caps = (capability if isinstance(capability, (tuple, list))
                    else (capability,))
            if not any(_authsvc().has_capability(chat_id, c, site)
                       for c in caps):
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def require_superadmin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        chat_id = current_chat_id()
        if not chat_id:
            return redirect(url_for("auth.login", next=request.path))
        if not _authsvc().is_super_admin(chat_id):
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def resolve_site() -> str | None:
    """Active site from session cookie, revalidated against memberships.

    Never grants authority: unknown/revoked sites fall back to the first
    authorized site (or None). Switching the selector never changes the
    authorized membership set.
    """
    chat_id = current_chat_id()
    if not chat_id:
        return None
    svc = _authsvc()
    wanted = session.get("active_site")
    allowed = svc.sites_for_user(chat_id)
    if wanted in allowed:
        return wanted
    if allowed:
        session["active_site"] = allowed[0]
        return allowed[0]
    session.pop("active_site", None)
    return None


def set_active_site(site_id: str) -> bool:
    """Switch selector only if the site is in the authorized set."""
    chat_id = current_chat_id()
    if not chat_id:
        return False
    if site_id in _authsvc().sites_for_user(chat_id):
        session["active_site"] = site_id
        return True
    return False


def csrf_protect(view):
    """Require per-session CSRF token on POST (form field or header)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if request.method == "POST":
            db = _db()
            sid = request.cookies.get(webauth.SESSION_COOKIE)
            sess = webauth.get_session(db, sid)
            provided = request.form.get("csrf_token") or \
                request.headers.get("X-CSRF-Token")
            if not sess or not webauth.check_csrf(sess, provided):
                abort(403)
        return view(*args, **kwargs)
    return wrapper
