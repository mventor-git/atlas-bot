"""
Legacy Report Import Script — Bulk-import old labor reports into the database.

This script reads Excel files from a source directory (or individual files),
parses them into Report/ReportItem objects, and persists them to the database
with status=DRAFT and telegram_user="admin_import".

Key design decisions:
  - No event log entries are created for imported reports.
  - Reports are marked as DRAFT so they can be reviewed/finalized later.
  - The script only adds reports that don't already exist for that date.
  - Contractor data is preserved exactly as in the original Excel.
  - Supports multiple Excel formats and openpyxl fallback.

Usage:
    python scripts/import_legacy_reports.py --dir ./legacy_excel
    python scripts/import_legacy_reports.py --file ./old_report.xlsx
    python scripts/import_legacy_reports.py --dir ./legacy_excel --status final
    python scripts/import_legacy_reports.py --dir ./legacy_excel --dry-run  # No DB writes

Options:
    --dir DIR         Directory containing legacy Excel files (.xlsx, .xls)
    --file PATH       Single legacy Excel file to import
    --status STATUS   Status to assign (default: draft; options: draft, final, locked)
    --dry-run         Print what would be imported without writing to DB
    --config PATH     Override config file path
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.config.loader import ConfigLoader, ConfigurationError
from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.arabic_date_service import ArabicDateService
from app.utils.logger import setup_logger, get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Import legacy labor reports from Excel files into the database.",
        epilog="Reports are imported as DRAFT by default, with no event log entries.",
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Directory containing legacy Excel files (.xlsx, .xls)",
    )
    source_group.add_argument(
        "--file",
        type=str,
        default=None,
        help="Single legacy Excel file to import",
    )

    parser.add_argument(
        "--status",
        type=str,
        default="draft",
        choices=["draft", "final", "locked"],
        help="Status to assign to imported reports (default: draft)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be imported without writing to database",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Override path to config.yaml (default: config/config.yaml)",
    )
    return parser.parse_args()


def get_excel_files(source_dir: Path) -> list[Path]:
    """Get all Excel files from a directory.

    Args:
        source_dir: Directory to scan.

    Returns:
        Sorted list of Excel file paths.
    """
    patterns = ["*.xlsx", "*.xls"]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(source_dir.glob(pattern))

    # Sort for deterministic ordering
    files.sort()
    return files


def parse_report_from_excel(file_path: Path, arabic_date_service: ArabicDateService) -> Optional[Report]:
    """Parse a legacy Excel file into a Report object.

    Tries win32com first, falls back to openpyxl.

    Expected Excel layout (from the Daily Labor Report template):
      - Date cell: B7 (merged B7:C7)
      - Day cell: B5 (merged B5:C5)
      - Labor table starts at row 11 (row 10 is header)
      - Columns: B=Serial, C=Contractor, D=Type, E=Zone, F=Workers, G=Details

    Args:
        file_path: Path to the Excel file.
        arabic_date_service: Service for getting Arabic day names.

    Returns:
        Report object if parsing succeeds, None otherwise.
    """
    try:
        # Try openpyxl first for cross-platform compatibility
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active

        # Read date from cell B7 (merged)
        date_str = None
        date_cell = ws["B7"]
        if date_cell.value:
            if isinstance(date_cell.value, datetime):
                date_str = date_cell.value.strftime("%Y-%m-%d")
            else:
                # Try parsing as string
                from app.utils.date_parser import parse_date

                parsed = parse_date(str(date_cell.value))
                if parsed:
                    date_str = parsed.isoformat()

        if not date_str:
            # Fallback: try to extract date from filename
            date_str = _extract_date_from_filename(file_path.stem)

        if not date_str:
            logger.warning("Could not determine date for %s — skipping", file_path.name)
            wb.close()
            return None

        # Get day name
        day_name = arabic_date_service.get_arabic_day_name(date_str)

        # Read labor table rows
        items: list[ReportItem] = []
        row = 11  # First data row
        while True:
            contractor_cell = ws[f"C{row}"]
            if not contractor_cell.value:
                break  # Empty row = end of table

            contractor = str(contractor_cell.value).strip()
            if not contractor:
                row += 1
                continue

            type_val = ws[f"D{row}"].value
            zone_val = ws[f"E{row}"].value
            workers_val = ws[f"F{row}"].value
            details_val = ws[f"G{row}"].value

            # Parse workers
            workers = None
            if workers_val is not None:
                try:
                    workers = int(float(str(workers_val)))
                except (ValueError, TypeError):
                    pass

            item = ReportItem(
                contractor=contractor,
                type=str(type_val).strip() if type_val else None,
                zone=str(zone_val).strip() if zone_val else None,
                workers=workers,
                details=str(details_val).strip() if details_val else None,
            )
            items.append(item)
            row += 1

        wb.close()

        if not items:
            logger.info("No data rows found in %s — creating empty report", file_path.name)

        report = Report(
            date=date_str,
            day=day_name,
            status=ReportStatus.DRAFT,  # Will be overridden if --status specified
            telegram_user="admin_import",
            items=items,
        )

        return report

    except ImportError:
        logger.error("openpyxl is required for legacy import. Install with: pip install openpyxl")
        return None
    except Exception as e:
        logger.error("Failed to parse %s: %s", file_path.name, e)
        return None


def _extract_date_from_filename(stem: str) -> Optional[str]:
    """Try to extract a date from the filename stem.

    Supports patterns like:
      - 2025-01-07
      - 20250107
      - 07-01-2025
      - Jan 07 2025
      - report_2025-01-07

    Args:
        stem: Filename without extension.

    Returns:
        ISO date string (YYYY-MM-DD) or None.
    """
    import re

    # Pattern 1: YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", stem)
    if m:
        return m.group(0)

    # Pattern 2: YYYYMMDD
    m = re.search(r"(\d{4})(\d{2})(\d{2})", stem)
    if m:
        y, mo, d = m.groups()
        # Validate month/day
        try:
            dt = datetime(int(y), int(mo), int(d))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Pattern 3: DD-MM-YYYY or DD/MM/YYYY
    m = re.search(r"(\d{2})[_-](\d{2})[_-](\d{4})", stem)
    if m:
        d, mo, y = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def validate_report(report: Report) -> list[str]:
    """Validate a parsed report before importing.

    Args:
        report: The parsed Report object.

    Returns:
        List of warning/error messages. Empty if valid.
    """
    warnings: list[str] = []

    if not report.date:
        warnings.append("Report has no date")

    if not report.day:
        warnings.append("Report has no day name")

    if not report.items:
        warnings.append("Report has no labor items (empty report)")

    # Check for duplicate contractors
    contractors_seen = set()
    for item in report.items:
        if item.contractor in contractors_seen:
            warnings.append(f"Duplicate contractor: {item.contractor}")
        contractors_seen.add(item.contractor)

    return warnings


def import_report(
    report: Report,
    repo: ReportRepository,
    target_status: ReportStatus,
    dry_run: bool,
) -> bool:
    """Import a single report into the database.

    Skips if a report already exists for the same date.

    Args:
        report: The Report to import.
        repo: Report repository for persistence.
        target_status: Status to assign to imported reports.
        dry_run: If True, only logs what would be done.

    Returns:
        True if imported (or would be imported in dry-run).
    """
    # Check for existing report
    existing = repo.get_by_date(report.date)
    if existing:
        logger.info(
            "SKIP [%s]: Report already exists (status=%s, user=%s)",
            report.date,
            existing.status.value,
            existing.telegram_user,
        )
        return False

    # Override status
    report.status = target_status

    # Validate
    warnings = validate_report(report)
    for w in warnings:
        logger.warning("  WARN [%s]: %s", report.date, w)

    if dry_run:
        logger.info(
            "DRY-RUN [%s]: Would import report with %d items (status=%s)",
            report.date,
            len(report.items),
            target_status.value,
        )
        return True

    # Persist
    try:
        saved = repo.add(report)
        logger.info(
            "IMPORTED [%s]: %d items, status=%s, id=%s",
            saved.date,
            len(saved.items) if saved.items else 0,
            saved.status.value,
            saved.id,
        )
        return True
    except Exception as e:
        logger.error("FAILED [%s]: %s", report.date, e)
        return False


def main() -> None:
    """Main entry point for the legacy import script."""
    # Parse arguments
    args = parse_args()

    # Parse status
    target_status = ReportStatus(args.status)

    # Setup
    logger = setup_logger()
    logger.info("=" * 60)
    logger.info("Legacy Report Import Tool")
    logger.info("=" * 60)

    try:
        # Load config
        config = ConfigLoader.load(args.config)
        logger.info("Config loaded from: %s", args.config or ConfigLoader.DEFAULT_CONFIG_PATH)

        # Determine source files
        if args.dir:
            source_dir = Path(args.dir)
            if not source_dir.is_dir():
                logger.error("Source directory not found: %s", source_dir)
                sys.exit(1)
            files = get_excel_files(source_dir)
            logger.info("Found %d Excel files in %s", len(files), source_dir)
        elif args.file:
            file_path = Path(args.file)
            if not file_path.is_file():
                logger.error("File not found: %s", file_path)
                sys.exit(1)
            files = [file_path]
            logger.info("Single file: %s", file_path)
        else:
            files = []

        if not files:
            logger.info("No Excel files to import.")
            return

        # Initialize database and services
        logger.info("Initializing database...")
        db_manager = DatabaseManager(str(config.database_path))
        db_manager.run_migration()
        repo = ReportRepository(db_manager)
        arabic_date_service = ArabicDateService()

        # Parse and import each file
        imported_count = 0
        skipped_count = 0
        failed_count = 0

        logger.info("")
        logger.info("─" * 60)
        logger.info("Starting import (%s)...", "DRY RUN" if args.dry_run else "LIVE")
        logger.info("Target status: %s", target_status.value)
        logger.info("─" * 60)
        logger.info("")

        for file_path in files:
            try:
                report = parse_report_from_excel(file_path, arabic_date_service)
                if report is None:
                    logger.warning("FAILED to parse: %s", file_path.name)
                    failed_count += 1
                    continue

                success = import_report(report, repo, target_status, args.dry_run)
                if success:
                    imported_count += 1
                else:
                    skipped_count += 1

            except Exception as e:
                logger.error("Error processing %s: %s", file_path.name, e)
                failed_count += 1

        # Summary
        logger.info("")
        logger.info("═" * 60)
        logger.info("Import Summary")
        logger.info("═" * 60)
        logger.info("  Total files : %d", len(files))
        logger.info("  Imported    : %d", imported_count)
        logger.info("  Skipped     : %d", skipped_count)
        logger.info("  Failed      : %d", failed_count)
        if args.dry_run:
            logger.info("  (Dry run — no changes written to database)")
        logger.info("═" * 60)

        # Cleanup
        db_manager.close_all()

    except ConfigurationError as e:
        logger.critical("Configuration error: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.critical("Unexpected error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
