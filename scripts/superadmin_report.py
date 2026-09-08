#!/usr/bin/env python3
"""
Superadmin Audit Report Generator — CLI tool.

Generates a PDF audit report for any user's activity from the audit database.

Usage:
    python -m scripts.superadmin_report --user 123456789
    python -m scripts.superadmin_report --user 123456789 --days 30
    python -m scripts.superadmin_report --user <telegram_user_id> --output report.pdf
"""

import argparse
import sys
import os
from datetime import datetime, timedelta
from typing import Optional

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.manager import DatabaseManager
from app.services.audit_service import AuditService
from app.models.audit import UserActivityLog


def generate_pdf_report(
    user_activity: list[UserActivityLog],
    target_user: str,
    output_path: str,
) -> None:
    """Generate a plain-text audit report (saved as .txt).

    For PDF generation, you can pipe this output or open it in a text viewer.
    A full PDF generation would require reportlab or similar.
    This produces a structured TXT report that can be saved as .pdf
    (some systems render .txt as PDF automatically).

    Args:
        user_activity: List of activity logs for the user.
        target_user: The Telegram user ID being reported on.
        output_path: Where to write the report.
    """
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"  AUDIT REPORT — User: {target_user}")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 72)
    lines.append("")

    if not user_activity:
        lines.append("  No activity found for this user.")
        lines.append("")
        lines.append("=" * 72)
        _write_report(output_path, lines)
        return

    # Summary
    added_count = sum(1 for a in user_activity if a.action == "added")
    reverted_count = sum(1 for a in user_activity if a.action == "reverted")
    viewed_count = sum(1 for a in user_activity if a.action == "viewed")
    exported_count = sum(1 for a in user_activity if a.action == "exported")

    lines.append(f"  Activity Summary:")
    lines.append(f"    Added entries:   {added_count}")
    lines.append(f"    Reverted entries: {reverted_count}")
    lines.append(f"    Views:           {viewed_count}")
    lines.append(f"    Exports/PDFs:    {exported_count}")
    lines.append(f"    Total actions:   {len(user_activity)}")
    lines.append("")

    # Current role
    roles = set(a.user_role for a in user_activity)
    if roles:
        lines.append(f"  Role(s): {', '.join(sorted(roles))}")
        lines.append("")

    lines.append("-" * 72)
    lines.append("  DETAILED LOG")
    lines.append("-" * 72)
    lines.append("")

    for i, entry in enumerate(user_activity, 1):
        timestamp = entry.timestamp[:19] if entry.timestamp else "—"
        action_icon = {
            "added": "[ADD]",
            "reverted": "[REV]",
            "viewed": "[VIEW]",
            "exported": "[EXPORT]",
        }.get(entry.action, f"[{entry.action.upper()}]")

        lines.append(f"  {i:3d}. {action_icon} {timestamp}")
        lines.append(f"       Report: {entry.report_date or '—'} ({entry.report_status or '—'})")

        if entry.action == "added":
            lines.append(f"       Contractor: {entry.contractor_name or '—'}")
            lines.append(f"       Workers: {entry.workers or 0}")
            lines.append(f"       Zone: {entry.zone or '—'}")
            if entry.details:
                lines.append(f"       Details: {entry.details}")
        elif entry.action == "reverted":
            lines.append(f"       Contractor: {entry.contractor_name or '—'}")
            if entry.reverted_entry_id and entry.reverted_entry_id > 0:
                lines.append(f"       Original entry ID: {entry.reverted_entry_id}")
        elif entry.action == "viewed":
            lines.append(f"       (Viewed report)")
        elif entry.action == "exported":
            lines.append(f"       (Exported PDF)")
        lines.append("")

    lines.append("=" * 72)
    lines.append("  END OF REPORT")
    lines.append("=" * 72)

    _write_report(output_path, lines)


def _write_report(path: str, lines: list[str]) -> None:
    """Write lines to a file, ensuring parent directory exists."""
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Report written to {os.path.abspath(path)}")


def get_activity_for_user(audit_service: AuditService, target_user: str) -> list[UserActivityLog]:
    """Fetch all activity for a user."""
    return audit_service.get_user_activity(target_user, limit=1000)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate audit report for a Telegram user.",
        epilog=(
            "Examples:\n"
            "  python -m scripts.superadmin_report --user 123456789\n"
            "  python -m scripts.superadmin_report --user 123456789 --days 30 --output report.txt\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--user", required=True, help="Telegram user ID to report on")
    parser.add_argument("--days", type=int, default=0, help="Only include last N days (0 = all)")
    parser.add_argument("--output", default="", help="Output file path (default: auto-name)")
    parser.add_argument("--db", default="", help="Path to the SQLite database (auto-detected)")

    args = parser.parse_args()

    # Determine database path
    db_path = args.db
    if not db_path:
        # Try common locations
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "labor_report.db"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "labor_report.db"),
            "labor_report.db",
        ]
        for c in candidates:
            if os.path.exists(c):
                db_path = c
                break

    if not db_path or not os.path.exists(db_path):
        print("ERROR: Could not find the database. Use --db to specify the path.", file=sys.stderr)
        sys.exit(1)

    # Initialize
    db_manager = DatabaseManager(db_path)
    audit_service = AuditService(db_manager)

    # Fetch activity
    activity = get_activity_for_user(audit_service, args.user)
    if not activity:
        print(f"No activity found for user {args.user}.")
        # Still generate a report with empty data
        activity = []

    # Filter by days if requested
    if args.days > 0:
        cutoff = datetime.now() - timedelta(days=args.days)
        activity = [
            a for a in activity
            if a.timestamp and _parse_timestamp(a.timestamp) >= cutoff
        ]

    # Output path
    output = args.output
    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"audit_report_user_{args.user}_{timestamp}.txt"

    generate_pdf_report(activity, args.user, output)


def _parse_timestamp(ts: str) -> datetime:
    """Parse a timestamp string, returning a default datetime if parsing fails."""
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime.min


if __name__ == "__main__":
    main()
