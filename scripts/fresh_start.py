#!/usr/bin/env python3
"""
Fresh Start Utility — Deletes today's report and resets the database for a clean slate.

Usage:
    python scripts/fresh_start.py          # Delete today's report
    python scripts/fresh_start.py --all     # Delete ALL reports (complete reset)
    python scripts/fresh_start.py --dry-run # Show what would be deleted without actually deleting
"""
import sys
import argparse
from pathlib import Path
from datetime import date

# Ensure project root is in path
_root = Path(__file__).parent.parent.resolve()
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from app.database.manager import DatabaseManager
from app.config.loader import ConfigLoader


def main():
    parser = argparse.ArgumentParser(description="Reset the database for a fresh start.")
    parser.add_argument("--all", action="store_true", help="Delete ALL reports (complete reset)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without actually deleting")
    args = parser.parse_args()

    config = ConfigLoader.load()
    db_path = config.database_path

    if not db_path.exists():
        print("No database found. Nothing to clean.")
        return

    db = DatabaseManager(str(db_path))
    today = date.today().isoformat()

    if args.all:
        # Count all reports
        count = db.execute("SELECT COUNT(*) as cnt FROM reports").fetchone()
        total = count["cnt"] if count else 0
        
        if args.dry_run:
            print(f"[DRY RUN] Would delete ALL {total} reports.")
        else:
            # Delete all report items first (foreign key constraint)
            db.execute("DELETE FROM report_items")
            db.execute("DELETE FROM reports")
            db.execute("DELETE FROM event_log")
            db.execute("DELETE FROM report_versions")
            db.commit()
            print(f"Deleted ALL {total} reports. Database is completely fresh.")
    else:
        # Find today's report
        row = db.execute("SELECT * FROM reports WHERE date = ?", (today,)).fetchone()
        
        if row is None:
            print(f"No report found for today ({today}). Nothing to clean.")
            db.close_all()
            return
        
        report_id = row["id"]
        
        # Count items
        items_count = db.execute(
            "SELECT COUNT(*) as cnt FROM report_items WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        item_total = items_count["cnt"] if items_count else 0
        
        if args.dry_run:
            print(f"[DRY RUN] Would delete today's report (id={report_id}, status={row['status']}, items={item_total}).")
        else:
            # Delete items first, then the report
            db.execute("DELETE FROM report_items WHERE report_id = ?", (report_id,))
            db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            db.commit()
            print(f"Deleted today's report (id={report_id}, status={row['status']}, {item_total} items removed).")
            print("Ready for a fresh start!")

    db.close_all()


if __name__ == "__main__":
    main()
