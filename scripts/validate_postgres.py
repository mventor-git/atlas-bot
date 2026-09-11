"""Live-Postgres validation for Atlas-Bot (ticket-005-3 / 010).

Spins NO containers itself. Point it at a running Postgres:
    $env:DATABASE_URL = "postgresql://contractor:PASS@localhost:5432/atlas_bot"
    python scripts/validate_postgres.py
Or let it read POSTGRES_* from .env (docker compose defaults).

Covers: fresh schema, CRUD + RETURNING ids, ? translation, site tenants,
composite UNIQUE(date, site_id), DDL fixups (repo-owned tables),
hr role CHECK, isolation across sites. Uses a throwaway database and
drops it afterwards... actually it uses the configured DB and cleans
its own rows; run against a scratch database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

CHECKS: list[str] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append(name)
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"FAILED: {name}")


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url.startswith(("postgres://", "postgresql://")):
        if os.environ.get("POSTGRES_PASSWORD"):
            user = os.environ.get("POSTGRES_USER", "contractor")
            pw = os.environ["POSTGRES_PASSWORD"]
            db = os.environ.get("POSTGRES_DB", "atlas_bot")
            url = f"postgresql://{user}:{pw}@localhost:5432/{db}"
            os.environ["DATABASE_URL"] = url
        else:
            print("Set DATABASE_URL (or POSTGRES_PASSWORD in .env). Nothing to do.")
            return 2

    os.environ["SITE_ID"] = "pg-a"
    from app.database.manager import DatabaseManager
    from app.models.database import Report, ReportItem, ReportStatus
    from app.repositories.report_repository import ReportRepository
    from app.repositories.event_log_repository import EventLogRepository
    from app.repositories.audit_repository import AuditRepository
    from app.repositories.recent_contractor_repository import RecentContractorRepository
    from app.repositories.user_repository import UserRepository
    from app.models.database import User
    from app.utils.exceptions import DatabaseError

    db = DatabaseManager("ignored-on-postgres")
    check("manager boots on postgres", True)
    # Prologue: remove rows from previous runs (same scratch date/sites)
    db.execute("DELETE FROM report_items WHERE report_id IN "
               "(SELECT id FROM reports WHERE date = '2026-09-10')")
    db.execute("DELETE FROM reports WHERE date = '2026-09-10'")
    for chat in ("w1", "w2", "w3", "9002", "u1", "u2", "u3", "9001"):
        db.execute("DELETE FROM users WHERE chat_id = ?", (chat,))
    db.execute("DELETE FROM hr_requests WHERE requester_chat_id IN ('w1', 'u1')")
    db.execute("DELETE FROM payout_events WHERE request_id NOT IN (SELECT id FROM hr_requests)")
    db.execute("DELETE FROM deduction_events WHERE request_id NOT IN (SELECT id FROM hr_requests)")
    db.execute("DELETE FROM event_log WHERE telegram_user IN ('w1', 'u1')")
    db.execute("DELETE FROM user_activity_log WHERE telegram_user IN ('w1', 'u1')")
    db.commit()
    check("reports table exists", db.table_exists("reports"))
    check("site_id column exists", db.column_exists("reports", "site_id"))

    reports = ReportRepository(db)
    events = EventLogRepository(db)
    audit = AuditRepository(db)
    recent = RecentContractorRepository(db)
    users = UserRepository(db)
    check("repo-owned tables exist",
          db.table_exists("user_activity_log") and db.table_exists("recent_contractors"))

    rep = reports.add(Report(
        date="2026-09-10", day="Wednesday", status=ReportStatus.DRAFT,
        telegram_user="w1", site_id="pg-a",
        items=[ReportItem(contractor="PG2 Co", type="Civil", zone="Z",
                          workers=7, details="d")]))
    check("insert returns id (RETURNING)", isinstance(rep.id, int))
    check("item got id", isinstance(rep.items[0].id, int))
    check("read back scoped", reports.get_by_date("2026-09-10").id == rep.id)
    check("other site invisible", reports.get_by_date("2026-09-10", site_id="pg-b") is None)

    os.environ["SITE_ID"] = "pg-b"
    reports.add(Report(date="2026-09-10", day="Wednesday",
                       status=ReportStatus.DRAFT, telegram_user="w2",
                       site_id="pg-b"))
    check("same date second site ok (composite unique)", True)
    try:
        reports.add(Report(date="2026-09-10", day="Wednesday",
                           status=ReportStatus.DRAFT, telegram_user="w3",
                           site_id="pg-b"))
        check("duplicate same site rejected", False)
    except DatabaseError:
        check("duplicate same site rejected", True)

    entry = events.log(telegram_user="w1", action="test.pg")
    check("event stamped + id", isinstance(entry.id, int) and entry.site_id == "pg-b")

    audit.log_added(telegram_user="w1", user_role="admin", report_date="2026-09-10",
                    report_status="final", contractor_name="PG2 Co", workers=7)
    check("audit rows scoped",
          len(audit.get_by_user("w1")) == 1 and audit.get_recent_by_all_users() != [])

    recent.record_usage("w1", "PG2 Co")
    check("recent usage recorded", len(recent.get_recent_for_user("w1")) == 1)

    users.upsert(User(chat_id="9002", role="hr", site_id="pg-b"))
    check("hr role CHECK passes on postgres",
          users.get_by_chat_id("9002").role == "hr")
    check("users scoped by site",
          [u.chat_id for u in users.get_users_by_site("pg-b")] == ["9002"])

    from app.repositories.hr_repository import HRRepository
    from app.repositories.membership_repository import MembershipRepository
    from app.repositories.money_repository import MoneyRepository
    from app.models.hr import HRRequest
    from app.services.hr_service import HRService

    hr = HRService(HRRepository(db), MoneyRepository(db))
    check("hr_requests table exists", db.table_exists("hr_requests"))
    check("money tables exist",
          db.table_exists("payout_events") and db.table_exists("deduction_events"))
    check("memberships table exists", db.table_exists("user_site_memberships"))
    req = hr.request_advance("w1", "PG User", 1000, "test", site_id="pg-b")
    check("hr request stamped", isinstance(req.id, int) and req.site_id == "pg-b")
    hr.confirm_pm(req.id, "pm1", "PM", site_id="pg-b")
    hr.decide_hr(req.id, "hr1", "HR", True, deduction_month="2026-10", site_id="pg-b")
    hr.record_payout(req.id, 1000, "2026-09-10", "fin1", site_id="pg-b")
    hr.record_deduction(req.id, 1000, "2026-10", "pay1", site_id="pg-b")
    check("ledger derived closed",
          hr.financial_status(req.id, site_id="pg-b")["state"] == "closed")
    members = MembershipRepository(db)
    members.grant("w1", "pg-b", ["view_site_reports"])
    check("membership grant + read",
          members.find("w1", "pg-b") is not None)

    # cleanup own rows
    for r in reports.get_all(site_id="pg-a") + reports.get_all(site_id="pg-b"):
        reports.delete(r.id, site_id=r.site_id)
    users.delete("9002")
    db.close_all()
    check("cleanup ok", True)
    print(f"\nALL {len(CHECKS)} LIVE-PG CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
