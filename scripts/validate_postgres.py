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
        telegram_user="u1", site_id="pg-a",
        items=[ReportItem(contractor="PG Co", type="Civil", zone="Z",
                          workers=7, details="d")]))
    check("insert returns id (RETURNING)", isinstance(rep.id, int))
    check("item got id", isinstance(rep.items[0].id, int))
    check("read back scoped", reports.get_by_date("2026-09-10").id == rep.id)
    check("other site invisible", reports.get_by_date("2026-09-10", site_id="pg-b") is None)

    os.environ["SITE_ID"] = "pg-b"
    reports.add(Report(date="2026-09-10", day="Wednesday",
                       status=ReportStatus.DRAFT, telegram_user="u2",
                       site_id="pg-b"))
    check("same date second site ok (composite unique)", True)
    try:
        reports.add(Report(date="2026-09-10", day="Wednesday",
                           status=ReportStatus.DRAFT, telegram_user="u3",
                           site_id="pg-b"))
        check("duplicate same site rejected", False)
    except DatabaseError:
        check("duplicate same site rejected", True)

    entry = events.log(telegram_user="u1", action="test.pg")
    check("event stamped + id", isinstance(entry.id, int) and entry.site_id == "pg-b")

    audit.log_added(telegram_user="u1", user_role="admin", report_date="2026-09-10",
                    report_status="final", contractor_name="PG Co", workers=7)
    check("audit rows scoped",
          len(audit.get_by_user("u1")) == 1 and audit.get_recent_by_all_users() != [])

    recent.record_usage("u1", "PG Co")
    check("recent usage recorded", len(recent.get_recent_for_user("u1")) == 1)

    users.upsert(User(chat_id="9001", role="hr", site_id="pg-b"))
    check("hr role CHECK passes on postgres",
          users.get_by_chat_id("9001").role == "hr")
    check("users scoped by site",
          [u.chat_id for u in users.get_users_by_site("pg-b")] == ["9001"])

    # cleanup own rows
    for r in reports.get_all(site_id="pg-a") + reports.get_all(site_id="pg-b"):
        reports.delete(r.id, site_id=r.site_id)
    users.delete("9001")
    db.close_all()
    check("cleanup ok", True)
    print(f"\nALL {len(CHECKS)} LIVE-PG CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
