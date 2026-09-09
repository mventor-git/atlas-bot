"""History service for Atlas-Bot (LibreOffice-native, odfpy).

Maintains a history.ods file as a secondary record of all reports.
This allows quick viewing of report history without querying SQLite.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.table import Table, TableCell, TableRow
from odf.text import P

from app.models.database import Report, ReportStatus
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Headers for the history file
HISTORY_HEADERS = ["Date", "Day", "Status", "PDF", "Document", "Created At"]


def _cell_text(cell) -> str:
    return "/".join(
        "".join(str(n) for n in p.childNodes if n.nodeType == 3)
        for p in cell.getElementsByType(P)
    )


def _append_row(table, values: list) -> None:
    row = TableRow()
    for value in values:
        cell = TableCell()
        p = P()
        p.addText("" if value is None else str(value))
        cell.addElement(p)
        row.addElement(cell)
    table.addElement(row)


class HistoryService:
    """Service for maintaining the history.ods report registry.

    Appends a row to history.ods for every report action
    (generated or no_report). This is a secondary record alongside
    the SQLite database. The file is created with headers if needed.
    """

    def __init__(self, history_path: str | Path) -> None:
        """Initialize the history service.

        Args:
            history_path: Path to the history.ods file.
        """
        self._history_path = Path(history_path).resolve()
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("HistoryService initialized: %s", self._history_path)

    @property
    def history_path(self) -> Path:
        """Get the path to the history file."""
        return self._history_path

    def register_report(self, report: Report, pdf_path: Optional[str] = None, doc_path: Optional[str] = None) -> None:
        """Register a report in the history file.

        Args:
            report: The report to register.
            pdf_path: Path to the generated PDF (optional).
            doc_path: Path to the generated document (optional).
        """
        try:
            if self._history_path.exists():
                doc = load(str(self._history_path))
                table = doc.getElementsByType(Table)[0]
            else:
                doc = OpenDocumentSpreadsheet()
                table = Table(name="History")
                doc.spreadsheet.addElement(table)
                _append_row(table, HISTORY_HEADERS)

            status_label = "Generated" if report.status == ReportStatus.GENERATED else "No Report"
            _append_row(table, [
                report.date,
                report.day,
                status_label,
                pdf_path or "",
                doc_path or "",
                report.created_at or datetime.now().isoformat(),
            ])
            doc.save(str(self._history_path))
            logger.info("History updated: date=%s, status=%s", report.date, status_label)
        except Exception as e:
            raise DatabaseError(
                f"Failed to update history file: {e}",
                original_exception=e,
            ) from e

    def get_history(self) -> list[dict]:
        """Read all history entries from the file."""
        if not self._history_path.exists():
            return []
        try:
            doc = load(str(self._history_path))
            table = doc.getElementsByType(Table)[0]
            rows = []
            for row in table.getElementsByType(TableRow):
                vals = []
                for cell in row.getElementsByType(TableCell):
                    reps = int(cell.getAttribute("numbercolumnsrepeated") or 1)
                    vals.extend([_cell_text(cell)] * reps)
                rows.append(vals)
            if not rows:
                return []
            headers = [h or "" for h in rows[0]]
            return [
                {headers[i]: v for i, v in enumerate(r) if i < len(headers)}
                for r in rows[1:]
            ]
        except Exception as e:
            raise DatabaseError(
                f"Failed to read history file: {e}",
                original_exception=e,
            ) from e
