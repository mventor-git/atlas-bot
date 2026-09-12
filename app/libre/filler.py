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

# 024: fields resolved by header text so owners may insert columns anywhere.
# First header containing any alias wins; EN + common AR word stems.
# ponytail: if an owner label matches nothing, they rename the header or we
# add an alias here (upgrade: config table.header_aliases if that recurs).
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "contractor": ("contractor", "المقاول"),
    "type": ("type", "البند"),
    "zone": ("zone", "مكان"),
    "workers": ("worker", "العمال"),
    "craftsmen": ("craftsm", "حرفي"),  # stem: matches singular + plural
    "helpers": ("helper", "مساعد"),
    "details": ("detail", "التفصيلي"),
}
_REQUIRED = ("contractor", "workers")


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
                totals, cols = self._fill_items(table, items)
                self._fill_totals(table, totals, cols)
            return ots.save(doc, output_path)
        except (FileNotFoundError, LibreFillError):
            raise
        except Exception as e:
            raise LibreFillError(
                f"Failed to fill template: {e}", original_exception=e
            ) from e

    # --- internals ---

    def _value_cell(self, table, row_idx: int):
        """Cell holding the value next to a label (merged or text cell)."""
        row = table.getElementsByType(ots.TableRow)[row_idx]
        cells = ots.logical_cells(row)
        for cell in cells[1:]:
            if cell is None:
                continue
            span = int(cell.getAttribute("numbercolumnsspanned") or 1)
            if span > 1 or ots.cell_text(cell):
                return cell
        return next(c for c in cells[1:] if c is not None)

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

    @staticmethod
    def _locate_columns(header_texts: list[str]) -> dict[str, int | None]:
        """024: resolve fields to logical column indexes by header text.

        Owners may insert Craftsmen/Helpers columns anywhere; first header
        containing an alias wins. Missing required labels or a header that
        matches two fields fails loudly - a wrong layout must never print.
        Optional labels (type, zone, split columns, details) simply miss.
        """
        fold = [t.casefold() for t in header_texts]
        cols: dict[str, int | None] = {}
        for field, aliases in FIELD_ALIASES.items():
            hits = [i for i, t in enumerate(fold)
                    if any(a in t for a in aliases)]
            cols[field] = hits[0] if hits else None
        missing = [f for f in _REQUIRED if cols.get(f) is None]
        if missing:
            raise LibreFillError(
                f"Template headers missing required column(s): "
                f"{', '.join(missing)}")
        taken = [i for i in cols.values() if i is not None]
        if len(taken) != len(set(taken)):
            raise LibreFillError(
                "Template headers are ambiguous (one label matches two fields)")
        return cols

    def _fill_items(self, table, items: list) -> tuple[dict, dict]:
        """Write item rows; returns (totals, resolved columns)."""
        header_idx = ots.find_first(table, HEADER_MARKERS)
        totals_idx = ots.find_first(table, TOTALS_MARKERS, start=header_idx + 1)
        cols = self._locate_columns(
            [ots.cell_text(c) for c in
             ots.logical_cells(table.getElementsByType(ots.TableRow)[header_idx])])
        slots = list(range(header_idx + 1, totals_idx))
        while len(slots) < len(items):
            new_idx = ots.clone_row_after(table, slots[-1])
            slots.append(new_idx)
        totals = {"workers": 0, "craftsmen": None, "helpers": None}
        dedicated = cols["craftsmen"] is not None or cols["helpers"] is not None
        serial_idx = cols["contractor"] - 1 \
            if cols["contractor"] >= 1 else None
        for n, item in enumerate(items):
            cells = ots.logical_cells(
                table.getElementsByType(ots.TableRow)[slots[n]]
            )
            workers = item.workers or 0
            totals["workers"] += workers
            craftsmen, helpers = item.craftsmen, item.helpers
            if craftsmen is not None:
                totals["craftsmen"] = (totals["craftsmen"] or 0) + craftsmen
                if helpers is None:
                    helpers = workers - craftsmen
            if helpers is not None:
                totals["helpers"] = (totals["helpers"] or 0) + helpers

            def write(field: str, text: str) -> None:
                idx = cols.get(field)
                if idx is None:
                    return
                slot = cells[idx]
                if slot is None:
                    raise LibreFillError(
                        f"Template column for '{field}' is inside a merged "
                        "region")
                ots.set_cell_text(slot, text)

            if serial_idx is not None:
                if cells[serial_idx] is None:
                    raise LibreFillError(
                        "Serial column is inside a merged region")
                ots.set_cell_text(cells[serial_idx], str(n + 1))
            write("contractor", item.contractor or "")
            write("type", item.type or "")
            write("zone", item.zone or "")
            write("workers", str(workers))
            write("craftsmen", "" if craftsmen is None else str(craftsmen))
            write("helpers", "" if helpers is None else str(helpers))
            if dedicated:
                manual = item.details.strip() if item.details else ""
                write("details", manual)
            else:
                write("details", details_text(item))
        return totals, cols

    def _fill_totals(self, table, totals: dict, cols: dict) -> None:
        header_idx = ots.find_first(table, HEADER_MARKERS)
        totals_idx = ots.find_first(table, TOTALS_MARKERS, start=header_idx + 1)
        cells = ots.logical_cells(
            table.getElementsByType(ots.TableRow)[totals_idx]
        )
        for field in ("workers", "craftsmen", "helpers"):
            idx = cols.get(field)
            if idx is None:
                continue
            slot = cells[idx]
            if slot is None:
                raise LibreFillError(
                    f"Total column '{field}' is inside a merged region")
            value = totals[field]
            # Unknown splits stay blank; totals never fabricate zeros.
            ots.set_cell_text(slot, "" if value is None else str(value))
