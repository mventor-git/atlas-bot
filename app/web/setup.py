"""Fresh-start setup status (ticket-037).

Concept: ecom-erp SetupWizard (server-derived checklist, ordered journey
start-site -> read-manual -> checklist, guidance never lock-in). Every
item is recomputed from live data on each load, so the page can never
claim a lie and completion survives restarts.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


def _sites(config):
    out = []
    for entry in (config.sites or []):
        out.append(entry if isinstance(entry, dict) else {"id": str(entry)})
    return out


def setup_status(services, chat_id: str, manual_ack: bool) -> dict:
    """Return {items:[...], core_total, core_done, setup_complete, counts}."""
    config = services["config"]
    items = []

    sa = str(config.super_admin_chat_id or "")
    items.append({
        "key": "superadmin", "core": True, "done": sa not in ("", "0000000000"),
        "label": "Real superadmin id", "hint": "SUPERADMIN_CHAT_ID, not the placeholder.",
        "link": "/config/sites"})

    from app.web import auth as webauth
    linked = webauth.get_web_user_by_chat(services["db"], chat_id) is not None
    items.append({
        "key": "weblink", "core": True, "done": linked,
        "label": "Web login linked", "hint": "This login is bridged to your Atlas identity.",
        "link": "/config/users"})

    try:
        n_mem = len(services["membership_repo"].get_all(None) or [])
    except Exception:
        n_mem = 0
    items.append({
        "key": "memberships", "core": True, "done": n_mem > 0,
        "label": f"Memberships onboarded ({n_mem})",
        "hint": "Real users granted per-site memberships (/hr_member or DEV).",
        "link": "/config/memberships"})

    items.append({
        "key": "manual", "core": True, "done": bool(manual_ack),
        "label": "Operator manual read",
        "hint": "The full path from install to daily business, one printable page.",
        "link": "/static/manual.html"})

    sites = _sites(config)
    gaps = [s.get("id", "?") for s in sites
            if not (s.get("confirmer") and s.get("confirmer_fallback"))]
    items.append({
        "key": "confirmers", "core": False,
        "done": bool(sites) and not gaps,
        "label": "Attendance confirmers set",
        "hint": ("Missing confirmer/fallback on: " + ", ".join(gaps))
                if gaps else f"{len(sites)} site(s) configured.",
        "link": "/config/sites"})

    cal_gaps = [str(s.get("id")) for s in sites if not s.get("weekend")]
    items.append({
        "key": "calendars", "core": False,
        "done": bool(sites) and not cal_gaps,
        "label": "Working calendars explicit",
        "hint": ("Implicit Friday default on: " + ", ".join(cal_gaps))
                if cal_gaps else "Weekends explicit per site.",
        "link": "/config/calendar"})

    bdir = ROOT / "backups"
    snaps = sorted(bdir.glob("atlas-*.db*")) if bdir.exists() else []
    drill = (bdir / "RESTORE_DRILL_OK").exists()
    items.append({
        "key": "backups", "core": False, "done": bool(snaps) and drill,
        "label": "Backup + restore drill",
        "hint": "Daily snapshots kept; one restore drill before real users.",
        "link": "/config/health"})

    try:
        n_req = len(services["hr"].pending(None) or [])
    except Exception:
        n_req = 0
    core = [i for i in items if i["core"]]
    done = [i for i in core if i["done"]]
    return {"items": items, "core_total": len(core), "core_done": len(done),
            "setup_complete": len(done) == len(core) and len(core) > 0,
            "counts": {"memberships": n_mem, "pending_requests": n_req}}


def get_manual_ack(db, chat_id: str) -> bool:
    row = db.execute("SELECT manual_ack FROM web_users WHERE chat_id = ?",
                     (str(chat_id),)).fetchone()
    return bool(row and row["manual_ack"])


def set_manual_ack(db, chat_id: str, ack: bool) -> None:
    db.execute("UPDATE web_users SET manual_ack = ? WHERE chat_id = ?",
               (1 if ack else 0, str(chat_id)))
    db.commit()
