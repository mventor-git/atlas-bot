"""
PDF Generator for Labor-Report.

Uses Microsoft Excel COM automation (pywin32) to export
filled workbooks directly to PDF, preserving all formatting.

Requires Microsoft Excel to be installed on the system.
"""

from pathlib import Path
from typing import Optional

from app.models.config import AppConfig
from app.utils.exceptions import PDFError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PDFGenerator:
    """Generates PDF files from Excel workbooks via Excel COM automation.

    Wraps the win32com.client.Dispatch API to open an Excel workbook
    and export it as PDF using ExportAsFixedFormat.

    Properly handles pythoncom.CoInitialize/CoUninitialize for thread-safe
    COM usage — each call to convert_excel_to_pdf initializes COM on the
    calling thread and cleans up afterward.

    Usage:
        generator = PDFGenerator(config)
        pdf_path = generator.convert_excel_to_pdf("path/to/file.xlsx")
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize the PDF generator.

        Args:
            config: Application configuration with output paths.
        """
        self._config = config

    def convert_excel_to_pdf(
        self,
        excel_path: str,
        output_path: Optional[str] = None,
    ) -> str:
        """Convert an Excel workbook to PDF.

        Opens the Excel file via COM, exports it as PDF,
        then closes Excel without saving changes.

        Args:
            excel_path: Path to the source Excel file.
            output_path: Optional output PDF path. If None, uses
                        the excel_path with a .pdf extension in
                        the configured pdf_folder.

        Returns:
            Path to the generated PDF file.

        Raises:
            PDFError: If conversion fails or Excel is not installed.
            FileNotFoundError: If the source Excel file does not exist.
        """
        excel_path_obj = Path(excel_path)
        if not excel_path_obj.exists():
            raise FileNotFoundError(
                f"Excel file not found for PDF conversion: {excel_path}"
            )

        if output_path is None:
            output_path = str(
                self._config.pdf_folder_path / f"{excel_path_obj.stem}.pdf"
            )

        output_path_obj = Path(output_path)
        output_path_obj.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Converting to PDF: %s -> %s", excel_path, output_path,
        )

        # Import inside method — not available on all systems
        try:
            import win32com.client  # pyright: ignore[reportMissingImport]
            import pythoncom
        except ImportError as e:
            raise PDFError(
                "pywin32 is required for PDF generation. "
                "Install it with: pip install pywin32",
                original_exception=e,
            ) from e

        pythoncom.CoInitialize()
        excel_app = None
        success = False

        try:
            excel_app = win32com.client.Dispatch("Excel.Application")
            excel_app.Visible = False
            excel_app.DisplayAlerts = False
            excel_app.ScreenUpdating = False
            excel_app.EnableEvents = False
            excel_app.Interactive = False
            # Disable all macro/security dialogs (guard against unsupported versions)
            try:
                excel_app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
            except Exception:
                logger.warning("Could not set AutomationSecurity (not supported on this Excel version)")

            # Open with UpdateLinks=0 to suppress "Update Links?" dialog
            workbook = excel_app.Workbooks.Open(
                str(excel_path_obj.resolve()),
                UpdateLinks=0,       # Don't update external links
                ReadOnly=True,       # Open read-only
                IgnoreReadOnlyRecommended=True,  # Don't show "Open as read-only?" prompt
                Notify=False,        # Don't notify about link status
            )

            # Pre-delete existing output PDF to avoid overwrite dialogs
            if output_path_obj.exists():
                try:
                    output_path_obj.unlink()
                    logger.debug("Removed existing PDF: %s", output_path)
                except Exception as rm_e:
                    logger.warning("Could not remove existing PDF %s: %s", output_path, rm_e)

            workbook.ExportAsFixedFormat(0, str(output_path_obj.resolve()))
            workbook.Close(SaveChanges=False)
            excel_app.Quit()
            success = True

            resolved_path = str(output_path_obj.resolve())
            logger.info("PDF generated successfully: %s", resolved_path)
            return resolved_path

        except Exception as e:
            raise PDFError(
                f"Failed to convert Excel to PDF: {e}",
                original_exception=e,
            ) from e

        finally:
            if not success:
                # Clean up Excel on error path
                if excel_app is not None:
                    try:
                        excel_app.Quit()
                    except Exception:
                        pass
            pythoncom.CoUninitialize()
