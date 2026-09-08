"""
Old PDF Importer — Import legacy PDFs into the pdf_database archive.

Usage:
    python scripts/import_old_pdfs.py                          # Import with defaults
    python scripts/import_old_pdfs.py --source "D:\\DC\\Other logs\\Labor_Log\\.db"
    python scripts/import_old_pdfs.py --dry-run                 # Preview only, no changes
    python scripts/import_old_pdfs.py --help                    # Show help

This script scans the specified source directory for old-style PDF files
named ``Labor_DD-MM-YYYY.pdf``, copies them to the configured
``pdf_database`` directory with the new standardized naming
(``DD-MM-YYYY_elshams_labor_report.pdf``), and creates database entries
so the search function can find them by date.

Requires the project to be fully configured (config.yaml, database, etc.).
"""

import argparse
import sys
from pathlib import Path

# Ensure the project root is in sys.path
_project_root = Path(__file__).parent.parent.resolve()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.config.loader import ConfigLoader
from app.database.manager import DatabaseManager
from app.services.pdf_database_service import PdfDatabaseService
from app.utils.logger import setup_logger, get_logger


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Import legacy PDFs into the pdf_database archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/import_old_pdfs.py\n"
            '  python scripts/import_old_pdfs.py --source "D:\\\\DC\\\\Other logs\\\\Labor_Log\\\\.db"\n'
            "  python scripts/import_old_pdfs.py --dry-run\n"
        ),
    )
    parser.add_argument(
        "--source",
        type=str,
        default=r"D:\DC\Other logs\Labor_Log\.db",
        help="Source directory containing old PDF files (default: D:\\DC\\Other logs\\Labor_Log\\.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be imported without making changes",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to configuration file (default: config/config.yaml)",
    )
    return parser.parse_args()


def main() -> int:
    """Run the import process.

    Returns:
        0 on success, 1 on error.
    """
    args = parse_args()
    logger = setup_logger()

    logger.info("=" * 60)
    logger.info("Old PDF Import Tool")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN MODE — no changes will be made")
    logger.info("Source directory: %s", args.source)

    try:
        # Load configuration
        config = ConfigLoader.load(args.config)
        logger.info("Configuration loaded.")

        # Initialize database
        db_manager = DatabaseManager(str(config.database_path))
        db_manager.run_migration()
        logger.info("Database initialized.")

        # Create pdf_database service
        pdf_db_service = PdfDatabaseService(config, db_manager=db_manager)

        # Run import
        logger.info("Importing old PDFs from: %s", args.source)
        source_path = Path(args.source)

        if not source_path.is_dir():
            logger.error("Source directory not found: %s", source_path)
            return 1

        results = pdf_db_service.import_old_pdfs(
            source_path,
            dry_run=args.dry_run,
        )

        if args.dry_run:
            logger.info("DRY RUN complete. Would import %d PDF(s).", len(results))
            for date_str, status in sorted(results.items()):
                logger.info("  %s -> %s", date_str, status)
        else:
            # Count results
            imported = sum(1 for v in results.values() if v in ("imported", "updated"))
            skipped = sum(1 for v in results.values() if v.startswith("skip"))
            failed = sum(1 for v in results.values() if "fail" in v or "error" in v.lower())

            logger.info("=" * 60)
            logger.info("Import Summary:")
            logger.info("  Total processed: %d", len(results))
            logger.info("  Imported/Updated: %d", imported)
            logger.info("  Skipped: %d", skipped)
            logger.info("  Failed: %d", failed)

            if failed > 0:
                logger.warning("Failures:")
                for date_str, status in sorted(results.items()):
                    if "fail" in status or "error" in status.lower():
                        logger.warning("  %s: %s", date_str, status)

            logger.info("=" * 60)

        return 0

    except Exception as e:
        logger.critical("Import failed: %s", e, exc_info=True)
        print(f"FATAL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
