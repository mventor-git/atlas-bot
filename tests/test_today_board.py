"""BUILD-1 today-board smoke: board, rows, finalize, preview, override."""
import yaml
from pathlib import Path

import pytest

from app.models.database import User
from app.web import auth as webauth
from app.web import create_app

SA = "999"
A = "site-a"


@pytest.fixture()
def app(tmp_path):
    src = Path("config/config.yaml")
    cfg = yaml.safe_load(src.read_text(encoding="utf-8"))
    cfg["database"]["path"] = str(tmp_path / "t.db")
    cfg["auth"]["superadmin"] = SA
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    application = create_app(cfg_path)
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed(app, chat, role, caps):
    s = app.extensions["atlas_services"]
    s["user_repo"].upsert(User(id=None, chat_id=chat, role=role,
                               username=f"u{chat}", first_name=f"U{chat}",
                               created_at="2026-01-01", approved_by=SA,
                               approved_at="2026-01-01", site_id=A,
                               monthly_salary=None, updated_at="2026-01-01"))
    s["membership_repo"].grant(chat, A, caps)
    return webauth.create_web_user(s["db"], f"web{chat}", "password-1234",
                                   chat, created_by="test")


def _login(client, username):
    return client.post("/login",
                       data={"username": username, "password": "password-1234",
                             "next": "/"}, follow_redirects=False)


def _csrf(client):
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def _pm(client, app):
    _seed(app, "pm1", "project_manager",
          ["view_site_reports", "create_daily_report", "approve_daily_report",
           "override_offhours"])
    assert _login(client, "webpm1").status_code == 302


def test_board_renders(client, app):
    _pm(client, app)
    r = client.get("/today")
    assert r.status_code == 200, r.status_code
    assert b"Contractor Rows" in r.data


def test_add_row_then_finalize_then_preview(client, app):
    _pm(client, app)
    day = "2026-09-18"
    r = client.post(f"/today/{day}/rows",
                    data={"csrf_token": _csrf(client), "contractor": "ACME",
                          "type": "Civil", "zone": "Z1", "workers": "10",
                          "craftsmen": "6", "helpers": "4", "details": ""},
                    follow_redirects=False)
    assert r.status_code == 302, r.status_code
    s = app.extensions["atlas_services"]
    rep = s["reports"].get_by_date(day, site_id=A)
    assert rep is not None and len(rep.items) == 1
    assert rep.items[0].contractor == "ACME"
    r = client.post(f"/today/{day}/finalize",
                    data={"csrf_token": _csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 302
    rep = s["reports"].get_by_date(day, site_id=A)
    assert rep.status.value == "final"
    r = client.get(f"/today/{day}/v3-preview")
    assert r.status_code == 200, r.status_code
    assert r.data[:4] == b"PK\x03\x04"  # ODS zip bytes


def test_override_requires_reason(client, app):
    _pm(client, app)
    day = "2026-09-18"
    r = client.post(f"/today/{day}/override",
                    data={"csrf_token": _csrf(client), "reason": ""},
                    follow_redirects=False)
    assert r.status_code == 302
    with client.session_transaction() as sess:
        assert f"offhours_ok:{A}:{day}" not in sess
    r = client.post(f"/today/{day}/override",
                    data={"csrf_token": _csrf(client), "reason": "pour at night"},
                    follow_redirects=False)
    assert r.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get(f"offhours_ok:{A}:{day}") is True


def test_hr_cannot_edit_or_override(client, app):
    _seed(app, "hr1", "hr", ["view_site_reports"])
    assert _login(client, "webhr1").status_code == 302
    assert client.get("/today").status_code == 200
    assert client.post("/today/2026-09-18/rows",
                       data={"csrf_token": _csrf(client), "contractor": "X"},
                       follow_redirects=False).status_code == 403
    assert client.post("/today/2026-09-18/override",
                       data={"csrf_token": _csrf(client), "reason": "x"},
                       follow_redirects=False).status_code == 403
