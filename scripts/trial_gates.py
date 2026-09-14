"""Pre-trial gate checker (ticket-034 G1-G10) - read-only, one command:

    python scripts/trial_gates.py            # human report
    python scripts/trial_gates.py --quiet    # status lines + exit code only

Exit codes: 0 all PASS, 2 PENDING work remains, 1 hard FAIL.
Checks config/host/DB facts that CAN be machine-verified; anything that is
inherently human (choosing confirmers, ownership) is reported as PENDING
with the exact file/key to set, never guessed.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, PENDING, FAIL = "PASS", "PENDING", "FAIL"
rows = []


def add(gate, status, note):
    rows.append((gate, status, note))


def main() -> int:
    quiet = "--quiet" in sys.argv[1:]

    # --- config (G8 core) ---
    cfg = None
    try:
        from app.config.loader import ConfigLoader
        cfg = ConfigLoader.load(str(ROOT / "config" / "config.yaml"))
        add("G8 config loads", PASS, "config/config.yaml validated")
    except Exception as e:  # noqa: BLE001
        add("G8 config loads", FAIL, str(e)[:120])

    if cfg is not None:
        # G1: every site has confirmer + fallback chat IDs
        sites = list(getattr(cfg, "sites", None) or [])
        gaps = []
        for s in sites:
            if not isinstance(s, dict):
                continue
            sid = s.get("id", "?")
            if not (s.get("confirmer") and s.get("confirmer_fallback")):
                gaps.append(sid)
        if gaps:
            add("G1 attendance confirmers", PENDING,
                "config.yaml sites %s lack confirmer/confirmer_fallback" % gaps)
        else:
            add("G1 attendance confirmers", PASS, "%d sites configured" % len(sites))

        # G2: same humans own the assisted queue -> derived from G1
        add("G2 assisted-queue owner",
            PASS if not gaps else PENDING,
            "queue owner = confirmer set" if not gaps
            else "confirmer gaps above block this gate")

        # G6: working calendar explicit per site
        cal_gaps = [str(s.get("id")) for s in sites
                    if not (isinstance(s, dict) and s.get("weekend"))]
        add("G6 working calendars",
            PASS if not cal_gaps else PENDING,
            "weekends explicit" if not cal_gaps
            else "sites %s use implicit Friday default - confirm intent" % cal_gaps)

        # G8: env
        try:
            from app.config.loader import ConfigLoader as L
            tok = L.get_bot_token()
            add("G8 BOT_TOKEN present", PASS if len(tok) > 20 else FAIL,
                "present, length ok (value not printed)")
        except Exception as e:  # noqa: BLE001
            add("G8 BOT_TOKEN present", FAIL, str(e)[:80])
        sa = str(getattr(cfg, "super_admin_chat_id", "") or "")
        add("G8 superadmin real id",
            FAIL if sa in ("", "0000000000") else PASS,
            "placeholder '0000000000' - set in .env/config" if sa in ("", "0000000000")
            else "set")

        # G7: template contract (validator only; fill smoke via its own gate)
        try:
            from app.libre.validate import TemplateValidator
            v = TemplateValidator(cfg)
            bad = []
            for label, path in (("small", cfg.template.small_template),
                                ("medium", cfg.template.medium_template),
                                ("large", cfg.template.large_template)):
                rep = v.check_template(path)
                if not rep.passed:
                    bad.append(label)
            add("G7 template contract", PASS if not bad else FAIL,
                "small/medium/large valid" if not bad else "invalid: %s" % bad)
        except Exception as e:  # noqa: BLE001
            add("G7 template contract", FAIL, str(e)[:120])

        # --- DB-bound gates (G3/G4/G5) ---
        db_path = Path(os.environ.get("ATLAS_DB", str(cfg.database_path)))
        if not db_path.exists():
            note = "database %s absent - onboard members with /hr_member first" % db_path.name
            add("G4 real memberships", PENDING, note)
            add("G5 capability grants", PENDING, note)
            add("G3 notification owners", PENDING, note)
        else:
            from app.database.manager import DatabaseManager
            db = DatabaseManager(str(db_path))
            try:
                n_users = db.execute(
                    "SELECT COUNT(*) c FROM users WHERE role NOT IN "
                    "('pending','rejected')").fetchone()["c"]
                n_mem = db.execute(
                    "SELECT COUNT(*) c FROM user_site_memberships "
                    "WHERE status='active'").fetchone()["c"]
                add("G4 real memberships",
                    PASS if n_mem > 0 else PENDING,
                    "%d users, %d active memberships" % (n_users, n_mem))
                holders = db.execute(
                    "SELECT chat_id, capabilities FROM user_site_memberships "
                    "WHERE status='active'").fetchall()
                import json
                payroll_holders = [h["chat_id"] for h in holders
                                   if "manage_payroll" in
                                   (json.loads(h["capabilities"] or "[]"))]
                add("G3 notification owners",
                    PASS if payroll_holders else PENDING,
                    "payroll notice holders: %d" % len(payroll_holders))
                explicit = [h for h in holders
                            if json.loads(h["capabilities"] or "[]")]
                add("G5 capability grants", PASS if explicit else PENDING,
                    "%d/%d rows carry explicit grants (rest use role defaults)"
                    % (len(explicit), len(holders)))
            finally:
                db.close_all()
    else:
        for g in ("G1 attendance confirmers", "G2 assisted-queue owner",
                  "G6 working calendars", "G3 notification owners",
                  "G4 real memberships", "G5 capability grants",
                  "G7 template contract"):
            add(g, FAIL, "config unavailable")

    # --- G9 backups ---
    backups = sorted((ROOT / "backups").glob("atlas-*.db*"),
                     key=lambda p: p.stat().st_mtime) if (ROOT / "backups").exists() else []
    drill = (ROOT / "backups" / "RESTORE_DRILL_OK").exists()
    if backups and drill:
        age_h = (time.time() - backups[-1].stat().st_mtime) / 3600
        add("G9 backups scheduled",
            PASS if age_h < 30 else PENDING, "newest %.0fh old" % age_h)
        add("G9 restore drill done", PASS, "stamp backups/RESTORE_DRILL_OK")
    elif backups:
        add("G9 backups scheduled", PENDING,
            "%d backups exist; run restore drill + write stamp file" % len(backups))
        add("G9 restore drill done", PENDING, "no backups/RESTORE_DRILL_OK")
    else:
        add("G9 backups scheduled", PENDING,
            "none yet: python scripts/backup_db.py + host scheduler")
        add("G9 restore drill done", PENDING, "drill per docs/OPS-TRIAL.md")

    # --- G10 ---
    add("G10 no CLI-polling", PENDING,
        "human: bot must run as host process; verify after start-up")

    # --- report ---
    fail = any(s == FAIL for _, s, _ in rows)
    pend = any(s == PENDING for _, s, _ in rows)
    for gate, status, note in rows:
        print("%-8s %-28s %s" % (status, gate, note))
    n_pass = sum(1 for _, s, _ in rows if s == PASS)
    print("-- %d PASS / %d PENDING / %d FAIL -- %s"
          % (n_pass, sum(1 for _, s, _ in rows if s == PENDING),
             sum(1 for _, s, _ in rows if s == FAIL),
             "READY" if not (fail or pend) else
             ("BLOCKED" if fail else "PENDING OPERATOR WORK")))
    return 1 if fail else (2 if pend else 0)


if __name__ == "__main__":
    raise SystemExit(main())
