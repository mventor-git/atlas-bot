"""Check the current state of today's report."""
import sys
from pathlib import Path

# Ensure project root is in path
_root = Path(__file__).parent.parent.resolve()
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from app.database.manager import DatabaseManager
from app.config.loader import ConfigLoader
from datetime import date


def main():
    config = ConfigLoader.load()
    db_path = config.database_path
    
    print(f"Database path: {db_path.resolve()}")
    print(f"Database exists: {db_path.exists()}")
    
    if not db_path.exists():
        print("No database file found.")
        return
    
    db = DatabaseManager(str(db_path))
    today = date.today().isoformat()
    print(f"Today: {today}")
    
    row = db.execute("SELECT * FROM reports WHERE date = ?", (today,)).fetchone()
    if row:
        items = db.execute(
            "SELECT COUNT(*) as cnt FROM report_items WHERE report_id = ?",
            (row["id"],),
        ).fetchone()
        print(f"Report found: id={row['id']}, status={row['status']}, items={items['cnt'] if items else 0}")
        
        # Show all items
        item_rows = db.execute(
            "SELECT * FROM report_items WHERE report_id = ?", (row["id"],)
        ).fetchall()
        for item in item_rows:
            print(f"  - {item['contractor']}: {item['workers']} workers, zone={item['zone']}, details={item['details']}")
    else:
        print("No report for today.")
    
    # List all reports
    all_reports = db.execute("SELECT id, date, status FROM reports ORDER BY date DESC").fetchall()
    print(f"\nAll reports ({len(all_reports)} total):")
    for r in all_reports:
        print(f"  [{r['id']}] {r['date']} - {r['status']}")
    
    db.close_all()


if __name__ == "__main__":
    main()
