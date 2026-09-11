"""Daily report .ots template filler (LibreOffice-native, odfpy).

Locates day/date/data/totals by content markers so every template size
works without hardcoded addresses. Extra items clone a data row
(formatting preserved); the totals cell is recomputed in Python.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.libre import ots
from app.models.config import AppConfig
from app.models.database import Report
from app.utils.exceptions import LaborReportError
from app.utils.logger import get_logger

logger = get_logger(__name__)

DAY_MARKERS = ("Day:", "اليوم")
DATE_MARKERS = ("Date:", "التاريخ")
HEADER_MARKERS = ("Contractor", "اسم المقاول")
TOTALS_MARKERS = ("Total:", "الإجمالي")

# Logical columns B..G (0-based element index after repeat expansion)
COL_SERIAL, COL_CONTRACTOR, COL_TYPE, COL_ZONE, COL_WORKERS, COL_DETAILS = 1, 2, 3, 4, 5, 6


class LibreFillError(LaborReportError):
    """Raised when template filling fails."""

    @property
    def user_message(self) -> str:
        return "An error occurred while generating the report file. Please try again."


def details_text(item) -> str:
    """Print text for the "detailed number" column (G) (023).

    Manual details win verbatim; the craftsmen split renders as "C+H".
    Legacy items with no split print empty (unknown is never rendered
    as zeros).
    """
    if item.details and item.details.strip():
        return item.details
    if item.craftsmen is None:
        return ""
    helpers = item.helpers if item.helpers is not None else \
        (item.workers or 0) - item.craftsmen
    return f"{item.craftsmen}+{helpers}"


class TemplateFiller:
    """Fills a daily report .ots template with report data.

    Usage:
        filler = TemplateFiller(config)
        output_path = filler.fill(report)
    """

    TEMPLATE_CAPACITY = {"small": 7, "medium": 20, "large": 100}

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._template_path = Path("templates/contractor-daily-labor-template.ods")

    def _select_template(self, row_count: int) -> Path:
        return self._config.get_template_for_row_count(row_count)

    def fill(self, report: Report, output_path: Optional[str] = None) -> str:
        """Fill the template with report data and save as .ods.

        Args:
            report: The report containing date, day, and items.
            output_path: Optional output path (default: docs folder, ``<date>.ods``).

        Returns:
            Path to the saved .ods file.
        """
        items = list(report.items or [])
        template_path = self._select_template(len(items))
        self._template_path = template_path
        if not template_path.exists():
            raise FileNotFoundError(f"Template file not found: {template_path}")

        if output_path is None:
            output_path = str(
                self._config.docs_folder_path / f"{report.date}.ods"
            )

        logger.info("Filling template: %s -> %s (rows=%d)",
                    template_path, output_path, len(items))
        try:
            doc = ots.load_doc(template_path)
            table = ots.first_table(doc)
            self._fill_day_date(table, report)
            if items:
                total = self._fill_items(table, items)
                self._fill_totals(table, total)
            return ots.save(doc, output_path)
        except (FileNotFoundError, LibreFillError):
            raise
        except Exception as e:
            raise LibreFillError(
                f"Failed to fill template: {e}", original_exception=e
            ) from e

    # --- internals ---

    def _value_cell(self, table, row_idx: int):
        """Cell holding the value next to a label (first spanned/non-empty after col 0)."""
        row = table.getElementsByType(ots.TableRow)[row_idx]
        cells = ots.logical_cells(row)
        for cell in cells[1:]:
            span = int(cell.getAttribute("numbercolumnsspanned") or 1)
            if span > 1 or ots.cell_text(cell):
                return cell
        return cells[1]

    def _fill_day_date(self, table, report: Report) -> None:
        # Templates without day/date markers (e.g. empty-day) keep content as-is.
        try:
            ots.set_cell_text(
                self._value_cell(table, ots.find_first(table, DAY_MARKERS)), report.day
            )
        except ValueError:
            logger.debug("Day marker not in template, skipping")
        try:
            ots.set_cell_text(
                self._value_cell(table, ots.find_first(table, DATE_MARKERS)), report.date
            )
        except ValueError:
            logger.debug("Date marker not in template, skipping")

    def _fill_items(self, table, items: list) -> int:
        header_idx = ots.find_first(table, HEADER_MARKERS)
        totals_idx = ots.find_first(table, TOTALS_MARKERS, start=header_idx + 1)
        slots = list(range(header_idx + 1, totals_idx))
        while len(slots) < len(items):
            new_idx = ots.clone_row_after(table, slots[-1])
            slots.append(new_idx)
        total = 0
        for n, item in enumerate(items):
            cells = ots.logical_cells(
                table.getElementsByType(ots.TableRow)[slots[n]]
            )
            workers = item.workers or 0
            total += workers
            ots.set_cell_text(cells[COL_SERIAL], str(n + 1))
            ots.set_cell_text(cells[COL_CONTRACTOR], item.contractor or "")
            ots.set_cell_text(cells[COL_TYPE], item.type or "")
            ots.set_cell_text(cells[COL_ZONE], item.zone or "")
            ots.set_cell_text(cells[COL_WORKERS], str(workers))
            ots.set_cell_text(cells[COL_DETAILS], details_text(item))
        return total

    def _fill_totals(self, table, total: int) -> None:
        header_idx = ots.find_first(table, HEADER_MARKERS)
        totals_idx = ots.find_first(table, TOTALS_MARKERS, start=header_idx + 1)
        cells = ots.logical_cells(
            table.getElementsByType(ots.TableRow)[totals_idx]
        )
        ots.set_cell_text(cells[COL_WORKERS], str(total))
