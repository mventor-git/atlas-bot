"""
PDF Database Service — Centralized PDF archive for Labor-Report.

Provides unified storage and retrieval of all finalized/previewed PDFs
in a dedicated pdf_database directory with standardized naming:

    {DD-MM-YYYY}_{project_name}_labor_report.pdf

This service:
  1. Saves finalized PDFs (admin finalize, auto 5pm finalize, Friday/holiday)
     to the pdf_database directory.
  2. Imports legacy PDFs from the old pdf directory into the pdf_database
     with the new naming.
  3. Provides helper methods to compute PDF paths and filenames.
"""

import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from app.database.manager import DatabaseManager
from app.excel.template_filler import TemplateFiller
from app.models.config import AppConfig
from app.models.database import Report, ReportStatus
from app.pdf.generator import PDFGenerator
from app.repositories.report_repository import ReportRepository
from app.services.arabic_date_service import ArabicDateService
from app.utils.exceptions import DatabaseError, PDFError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PdfDatabaseService:
    """Centralized service for managing the PDF database archive.

    All finalized PDFs are stored in the configured pdf_database
    directory with a consistent filename format that includes the
    project name and date for easy identification and search.

    Usage:
        service = PdfDatabaseService(config, filler, generator, db_manager)
        pdf_path = service.save_finalized_pdf(report)
        service.import_old_pdfs("D:/DC/Other logs/Labor_Log/.db")
    """

    # Pattern for old-style filenames: Labor_DD-MM-YYYY.pdf
    OLD_PDF_PATTERN = re.compile(r"^Labor_(\d{2})-(\d{2})-(\d{4})\.pdf$", re.IGNORECASE)

    def __init__(
        self,
        config: AppConfig,
        template_filler: Optional[TemplateFiller] = None,
        pdf_generator: Optional[PDFGenerator] = None,
        db_manager: Optional[DatabaseManager] = None,
    ) -> None:
        """Initialize the PDF database service.

        Args:
            config: Application configuration (for pdf_database path and project name).
            template_filler: Optional TemplateFiller for generating PDFs from reports.
            pdf_generator: Optional PDFGenerator for Excel-to-PDF conversion.
            db_manager: Optional DatabaseManager for creating DB entries during import.
        """
        self._config = config
        self._filler = template_filler
        self._generator = pdf_generator
        self._db_manager = db_manager
        self._project_name = config.project.name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_pdf_filename(self, date_str: str) -> str:
        """Get the standardized PDF filename for a given date.

        Format: ``{DD-MM-YYYY}_{project_name}_labor_report.pdf``

        Example: ``12-07-2026_elshams_labor_report.pdf``

        Args:
            date_str: Date in YYYY-MM-DD format.

        Returns:
            Filename string (e.g., '12-07-2026_elshams_labor_report.pdf').
        """
        try:
            dt = date.fromisoformat(date_str)
            display_date = dt.strftime("%d-%m-%Y")
        except (ValueError, TypeError):
            display_date = date_str
        return f"{display_date}_{self._project_name}_labor_report.pdf"

    def get_pdf_path(self, date_str: str) -> Path:
        """Get the full path to the PDF database file for a given date.

        Args:
            date_str: Date in YYYY-MM-DD format.

        Returns:
            Full ``Path`` to the PDF file in the pdf_database directory.
        """
        folder = self._config.pdf_database_folder_path
        return folder / self.get_pdf_filename(date_str)

    def save_finalized_pdf(
        self,
        report: Report,
        source_pdf_path: Optional[str] = None,
    ) -> Optional[str]:
        """Save a finalized report's PDF to the pdf_database archive.

        If ``source_pdf_path`` is provided, the file is copied into the
        archive. Otherwise, a new PDF is generated from the report data
        using TemplateFiller + PDFGenerator.

        After saving, ``report.pdf_path`` is updated to point to the
        archived PDF.

        Args:
            report: The finalized/locked report.
            source_pdf_path: Optional path to an existing PDF to archive.
                            If None, a new PDF is generated.

        Returns:
            Path to the archived PDF file, or None if generation/copy failed.
        """
        dest_path = self.get_pdf_path(report.date)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if source_pdf_path and Path(source_pdf_path).exists():
                # Copy existing PDF into the archive
                shutil.copy2(source_pdf_path, str(dest_path))
                logger.info(
                    "Copied PDF to archive: %s -> %s",
                    source_pdf_path, dest_path,
                )
            elif self._filler and self._generator and report.items:
                # Generate a new PDF from the report data
                excel_path = str(
                    self._config.excel_folder_path / f"{report.date}.xlsx"
                )
                filled_excel = self._filler.fill(report, output_path=excel_path)
                self._generator.convert_excel_to_pdf(
                    filled_excel, output_path=str(dest_path),
                )
                logger.info(
                    "Generated and archived PDF: %s -> %s",
                    filled_excel, dest_path,
                )
            else:
                # No source and no generation capability — just record the path
                logger.warning(
                    "No PDF source or generator available for %s. "
                    "PDF path will be recorded but file may not exist.",
                    report.date,
                )
                return None

            # Verify the file was created
            if not dest_path.exists():
                logger.error("PDF was not created at %s", dest_path)
                return None

            # Update the report's pdf_path to point to the archive
            pdf_path_str = str(dest_path.resolve())
            report.pdf_path = pdf_path_str
            logger.info(
                "PDF archived: date=%s, path=%s",
                report.date, pdf_path_str,
            )
            return pdf_path_str

        except (PDFError, OSError, Exception) as e:
            logger.error(
                "Failed to save PDF to archive for %s: %s",
                report.date, e,
            )
            return None

    def import_old_pdfs(
        self,
        source_dir: str | Path,
        *,
        telegram_user: str = "system",
        status: ReportStatus = ReportStatus.LOCKED,
        dry_run: bool = False,
    ) -> dict[str, str]:
        """Import legacy PDFs from an old directory into the pdf_database.

        Scans ``source_dir`` for files matching the old naming pattern
        (``Labor_DD-MM-YYYY.pdf``), copies them into the pdf_database
        with the new standardized name, and creates database entries so
        the search function can find them by date.

        Args:
            source_dir: Directory containing old PDF files.
            telegram_user: Telegram user to attribute the import to.
            status: Report status to assign to imported entries
                    (default: LOCKED since they are historical finalized reports).
            dry_run: If True, only print what would be done without copying.

        Returns:
            Dict mapping date strings (YYYY-MM-DD) to result messages.

        Raises:
            FileNotFoundError: If source_dir does not exist.
        """
        source = Path(source_dir)
        if not source.is_dir():
            raise FileNotFoundError(f"Source directory not found: {source_dir}")

        results: dict[str, str] = {}
        repo: Optional[ReportRepository] = None
        if self._db_manager is not None and not dry_run:
            repo = ReportRepository(self._db_manager)

        # Ensure the pdf_database directory exists
        dest_root = self._config.pdf_database_folder_path
        if not dry_run:
            dest_root.mkdir(parents=True, exist_ok=True)

        # Scan for old PDFs
        pdf_files = sorted(source.glob("Labor_*.pdf"))
        if not pdf_files:
            logger.warning("No old PDFs found in %s", source_dir)
            return {}

        logger.info("Found %d old PDF(s) in %s", len(pdf_files), source_dir)

        for pdf_path in pdf_files:
            match = self.OLD_PDF_PATTERN.match(pdf_path.name)
            if not match:
                logger.debug("Skipping non-matching file: %s", pdf_path.name)
                continue

            day_str, month_str, year_str = match.group(1), match.group(2), match.group(3)
            try:
                dt = date(int(year_str), int(month_str), int(day_str))
            except (ValueError, TypeError):
                logger.warning("Invalid date in filename: %s", pdf_path.name)
                results[pdf_path.name] = "invalid_date"
                continue

            date_iso = dt.isoformat()  # YYYY-MM-DD
            dest_filename = self.get_pdf_filename(date_iso)
            dest_path = dest_root / dest_filename

            if dry_run:
                logger.info(
                    "[DRY RUN] Would copy %s -> %s",
                    pdf_path, dest_path,
                )
                results[date_iso] = "dry_run"
                continue

            # Copy the PDF to the archive
            try:
                shutil.copy2(str(pdf_path.resolve()), str(dest_path.resolve()))
                logger.info("Copied %s -> %s", pdf_path.name, dest_path.name)
            except OSError as e:
                logger.error("Failed to copy %s: %s", pdf_path.name, e)
                results[date_iso] = f"copy_failed: {e}"
                continue

            # Create a DB entry for the report (if repository is available)
            if repo is not None:
                try:
                    existing = repo.get_by_date(date_iso)
                    if existing is not None:
                        # Update existing report with PDF path
                        existing.pdf_path = str(dest_path.resolve())
                        if existing.status == ReportStatus.DRAFT:
                            existing.status = status
                        repo.update(existing, force=True)
                        logger.info(
                            "Updated existing report for %s with PDF path",
                            date_iso,
                        )
                        results[date_iso] = "updated"
                    else:
                        # Create a new report entry
                        day_name = ArabicDateService.get_arabic_day_name(dt)
                        new_report = Report(
                            date=date_iso,
                            day=day_name,
                            status=status,
                            telegram_user=telegram_user,
                            pdf_path=str(dest_path.resolve()),
                        )
                        repo.add(new_report)
                        logger.info(
                            "Created report entry for %s with PDF path",
                            date_iso,
                        )
                        results[date_iso] = "imported"
                except DatabaseError:
                    logger.warning(
                        "Report for %s already exists, skipping DB entry",
                        date_iso,
                    )
                    results[date_iso] = "skipped_duplicate"
                except Exception as e:
                    logger.error(
                        "Failed to create DB entry for %s: %s",
                        date_iso, e,
                    )
                    results[date_iso] = f"db_failed: {e}"
            else:
                results[date_iso] = "copied_no_db"

        # Summary
        imported = sum(1 for v in results.values() if v in ("imported", "updated", "copied_no_db"))
        logger.info(
            "Import complete: %d/%d PDFs processed, %d imported",
            len(results), len(pdf_files), imported,
        )
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_old_date_from_filename(filename: str) -> Optional[str]:
        """Parse a date from an old-style PDF filename.

        Args:
            filename: Old filename like ``Labor_12-07-2026.pdf``.

        Returns:
            ISO date string (YYYY-MM-DD) or None.
        """
        match = PdfDatabaseService.OLD_PDF_PATTERN.match(filename)
        if not match:
            return None
        day, month, year = match.group(1), match.group(2), match.group(3)
        try:
            dt = date(int(year), int(month), int(day))
            return dt.isoformat()
        except (ValueError, TypeError):
            return None
