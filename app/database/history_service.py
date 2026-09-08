"""
History Excel service for Labor-Report.

Maintains a history.xlsx file as a secondary record of all reports.
This allows quick viewing of report history without querying SQLite.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from app.models.database import Report, ReportStatus
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Headers for the history Excel file
HISTORY_HEADERS = ["Date", "Day", "Status", "PDF", "Excel", "Created At"]


class HistoryExcelService:
    """Service for maintaining the history.xlsx report registry.

    Appends a row to history.xlsx for every report action
    (generated or no_report).

    This is a secondary record alongside the SQLite database,
    providing easy Excel-based browsing of report history.

    The file is created with headers if it doesn't exist.
    """

    def __init__(self, history_path: str | Path) -> None:
        """Initialize the history service.

        Args:
            history_path: Path to the history.xlsx file.
        """
        self._history_path = Path(history_path).resolve()
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("HistoryExcelService initialized: %s", self._history_path)

    @property
    def history_path(self) -> Path:
        """Get the path to the history file."""
        return self._history_path

    def register_report(self, report: Report, pdf_path: Optional[str] = None, excel_path: Optional[str] = None) -> None:
        """Register a report in the history Excel file.

        Appends a row with report metadata.

        Args:
            report: The report to register.
            pdf_path: Path to the generated PDF (optional).
            excel_path: Path to the generated Excel (optional).

        Raises:
            DatabaseError: If writing to the history file fails.
        """
        try:
            import openpyxl
        except ImportError:
            logger.warning("openpyxl not available; history.xlsx will not be updated.")
            return

        try:
            # Open or create workbook
            if self._history_path.exists():
                wb = openpyxl.load_workbook(self._history_path)
                ws = wb.active
            else:
                wb = openpyxl.Workbook()
                ws = wb.active
                if ws is not None:
                    ws.title = "History"
                    ws.append(HISTORY_HEADERS)

            if ws is None:
                raise DatabaseError("Failed to get active worksheet in history file.")

            # Append row
            status_label = "Generated" if report.status == ReportStatus.GENERATED else "No Report"
            ws.append([
                report.date,
                report.day,
                status_label,
                pdf_path or "",
                excel_path or "",
                report.created_at or datetime.now().isoformat(),
            ])

            wb.save(str(self._history_path))
            wb.close()
            logger.info("History updated: date=%s, status=%s", report.date, status_label)

        except Exception as e:
            raise DatabaseError(
                f"Failed to update history file: {e}",
                original_exception=e,
            ) from e

    def get_history(self) -> list[dict]:
        """Read all history entries from the Excel file.

        Returns:
            List of dictionaries with history entries.

        Raises:
            DatabaseError: If reading the history file fails.
        """
        try:
            import openpyxl
        except ImportError:
            logger.warning("openpyxl not available; cannot read history.")
            return []

        if not self._history_path.exists():
            return []

        try:
            wb = openpyxl.load_workbook(self._history_path, read_only=True)
            ws = wb.active
            if ws is None:
                return []

            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return []

            # First row is headers
            headers = [str(h) if h else "" for h in rows[0]]
            result = []
            for row in rows[1:]:
                entry = {}
                for i, value in enumerate(row):
                    if i < len(headers):
                        entry[headers[i]] = value
                result.append(entry)

            wb.close()
            return result

        except Exception as e:
            raise DatabaseError(
                f"Failed to read history file: {e}",
                original_exception=e,
            ) from e
