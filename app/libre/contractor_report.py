"""Contractor period report .ots filler (LibreOffice-native, odfpy).

Same public shape as the legacy filler: ``fill(contractor_name,
start_date, end_date, entries, output_path)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.libre import ots
from app.libre.filler import LibreFillError
from app.utils.logger import get_logger

logger = get_logger(__name__)

INFO_MARKER = "Contractor:"
HEADER_MARKER = "Workers"
SUMMARY_PREFIX = "Summary:"


class ContractorReportFiller:
    """Fills the contractor period report template with data."""

    def __init__(self, template_path: str | Path) -> None:
        self._template_path = Path(template_path)

    def fill(
        self,
        contractor_name: str,
        start_date: str,
        end_date: str,
        entries: list[dict[str, Any]],
        output_path: str | Path,
    ) -> Path:
        """Fill the template and save to ``output_path``."""
        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Template file not found: {self._template_path}"
            )
        try:
            doc = ots.load_doc(self._template_path)
            table = ots.first_table(doc)
            rows = table.getElementsByType(ots.TableRow)

            info_idx = ots.find_row(table, INFO_MARKER)
            info_cells = ots.logical_cells(rows[info_idx])
            ots.set_cell_text(
                info_cells[0],
                f"Contractor: {contractor_name}  |  Period: {start_date} to {end_date}",
            )

            header_idx = ots.find_row(table, HEADER_MARKER, start=info_idx + 1)
            first_data = header_idx + 1
            # Capture style from the sample row, then drop all sample rows
            rows = table.getElementsByType(ots.TableRow)
            sample = rows[first_data]
            slots: list[int] = []
            while len(rows) > first_data and ots.row_text(rows[first_data]).strip():
                table.removeChild(rows[first_data])
                rows = table.getElementsByType(ots.TableRow)
            for _ in range(max(len(entries), 1)):
                rows = table.getElementsByType(ots.TableRow)
                at = slots[-1] if slots else first_data - 1
                slots.append(ots.insert_blank_row(table, at, like=sample))

            total = 0
            rows = table.getElementsByType(ots.TableRow)
            for i, entry in enumerate(entries):
                w = entry.get("workers", 0) or 0
                total += w
                cells = ots.logical_cells(rows[slots[i]])
                vals = [
                    str(entry.get("date", "")),
                    str(w),
                    str(entry.get("zone", "")),
                    str(entry.get("details", "")),
                    str(entry.get("added_by", "")),
                    str(entry.get("role", "")),
                ]
                for col, val in zip(range(1, 7), vals):
                    if col < len(cells):
                        ots.set_cell_text(cells[col], val)

            # Summary row right after data
            summary_idx = first_data + max(len(entries), 1)
            rows = table.getElementsByType(ots.TableRow)
            if summary_idx >= len(rows):
                summary_idx = ots.clone_row_after(table, len(rows) - 1)
                rows = table.getElementsByType(ots.TableRow)
            summary_cells = ots.logical_cells(rows[summary_idx])
            ots.set_cell_text(
                summary_cells[0],
                f"{SUMMARY_PREFIX} {len(entries)} entries, {total} total workers",
            )

            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            ots.save(doc, out)
            logger.info("Contractor report saved to %s", out)
            return out
        except (FileNotFoundError, LibreFillError):
            raise
        except Exception as e:
            raise LibreFillError(
                f"Failed to fill contractor report: {e}", original_exception=e
            ) from e
