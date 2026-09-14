"""SQLite database backup (pre-trial gate G9).

One-shot snapshot for the deploy host. Live Postgres is NOT silently
skipped: it exits loudly with the pg_dump command to run instead
(formal PG verification is roadmap Phase 3.1).

Usage:
    python scripts/backup_db.py [--db PATH] [--dest backups] [--keep 7]

Default --db: DATABASE_URL if Postgres (handled as above), else the
configured SQLite file database/atlas_bot.db next to the repo root.
The copy is verified with PRAGMA integrity_check before reporting PASS.
Exit 0 on success, 1 on failure, 2 for Postgres-needs-pg_dump.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "database" / "atlas_bot.db"


def snapshot(db_path: Path, dest_dir: Path) -> Path:
    """VACUUM-style copy of a live SQLite DB into dest_dir. Returns path."""
    if not db_path.exists():
        raise FileNotFoundError(f"Source DB missing: {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"atlas-{stamp}.db"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(out))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    check = sqlite3.connect(str(out))
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        out.unlink(missing_ok=True)
        raise RuntimeError(f"Backup failed integrity_check: {ok}")
    return out


def prune(dest_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` atlas-*.db files. Returns removed."""
    files = sorted(dest_dir.glob("atlas-*.db"), key=lambda p: p.name,
                   reverse=True)
    removed = []
    for old in files[keep:]:
        old.unlink(missing_ok=True)
        removed.append(old)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup Atlas SQLite DB.")
    parser.add_argument("--db", default=None, help="SQLite file to back up")
    parser.add_argument("--dest", default=str(REPO / "backups"),
                        help="Backup directory (gitignored)")
    parser.add_argument("--keep", type=int, default=7,
                        help="How many newest backups to retain")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith(("postgres://", "postgresql://")):
        print("Postgres detected in DATABASE_URL. Use the native tool:")
        print("  pg_dump \"$DATABASE_URL\" -Fc -f \"backups/atlas-$(date "
              "+%Y%m%d-%H%M%S).dump\"")
        print("Live-PG backup/restore drills are roadmap Phase 3.1.")
        return 2
    db = Path(args.db) if args.db else DEFAULT_DB
    try:
        out = snapshot(db, Path(args.dest))
    except (FileNotFoundError, RuntimeError) as e:
        print(f"FAIL backup: {e}")
        return 1
    removed = prune(Path(args.dest), max(1, args.keep))
    size_kb = out.stat().st_size // 1024
    print(f"PASS backup -> {out} ({size_kb} KB, integrity ok)")
    for gone in removed:
        print(f"pruned {gone.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
