"""Production template validation gate (030).

Contract between the shipped daily templates and the report filler:
structure, headers (header-driven, same `columns` rules the filler uses),
totals, day/date cells, and a fill smoke test. Failures name the file,
the field, what was found, and why it matters - never a bare
"Template invalid".

Covered files (daily contract only): the configured small/medium/large
templates. empty-day and contractor-period templates are different
contracts and are validated only when explicitly passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as _dataclass_field
from pathlib import Path

from app.libre import ots
from app.libre.columns import TemplateColumnError, locate_columns
from app.libre.filler import DATE_MARKERS, DAY_MARKERS, HEADER_MARKERS, TOTALS_MARKERS
from app.models.database import Report, ReportItem, ReportStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TemplateFailure:
    file: str
    check: str
    expected: str
    found: str

    def __str__(self) -> str:
        return (f"{self.file}: {self.check} - expected {self.expected}, "
                f"found {self.found}")


@dataclass
class TemplateReport:
    file: str
    passed: bool
    failures: list = _dataclass_field(default_factory=list)
    filled_rows: int = 0


def _col_letter(idx: int) -> str:
    name = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


class TemplateValidator:
    """Structure + header + fill-smoke validation for daily templates."""

    def __init__(self, config=None):
        self._config = config

    def check_template(self, path: str | Path) -> TemplateReport:
        """Validate one template file; never raises, collects failures."""
        name = str(path)
        failures: list[TemplateFailure] = []
        fail = lambda check, expected, found: failures.append(  # noqa: E731
            TemplateFailure(name, check, expected, found))

        try:
            doc = ots.load_doc(path)
        except Exception as e:
            fail("file readable", "loadable .ots/.ods", str(e))
            return TemplateReport(name, False, failures)
        try:
            table = ots.first_table(doc)
        except ValueError:
            fail("sheet present", "at least one table", "no tables")
            return TemplateReport(name, False, failures)

        rows = table.getElementsByType(ots.TableRow)
        try:
            header_idx = ots.find_first(table, HEADER_MARKERS)
        except ValueError:
            fail("header row", "row containing 'Contractor' label",
                 "no Contractor header row")
            return TemplateReport(name, False, failures)

        header_texts = [ots.cell_text(c) for c in ots.logical_cells(rows[header_idx])]
        try:
            cols = locate_columns(header_texts)
        except TemplateColumnError as e:
            matches = ", ".join(e.matches) if e.matches else "none"
            fail(f"header '{e.field}'", f"exactly one matching header "
                 f"(aliases: {e.reason})", f"matches at {matches}")
            return TemplateReport(name, False, failures)

        # split columns are REQUIRED on production templates (024 contract):
        # writable dedicated numeric fields, never the old composite fallback.
        for split_field in ("craftsmen", "helpers"):
            if cols.get(split_field) is None:
                fail(f"header '{split_field}'",
                     "dedicated numeric column header",
                     "missing (composite fallback is legacy-only)")

        # serial must be derivable (column left of contractor)
        if cols["contractor"] < 1:
            fail("serial column", "a column left of Contractor",
                 f"contractor at col{_col_letter(cols['contractor'])}")

        # totals row must exist below the header and carry the label
        try:
            totals_idx = ots.find_first(table, TOTALS_MARKERS, start=header_idx + 1)
        except ValueError:
            fail("totals row", "row with 'Total' label below header",
                 "no totals row")
            return TemplateReport(name, False, failures)

        # at least one data slot between header and totals
        if totals_idx - header_idx < 2:
            fail("data rows", ">=1 data row between header and totals",
                 "header directly above totals")

        # day/date cells must exist (the report stamps them)
        for markers, label in ((DAY_MARKERS, "day"), (DATE_MARKERS, "date")):
            try:
                ots.find_first(table, markers)
            except ValueError:
                fail(f"{label} cell", f"row with '{markers[0]}' label",
                     f"no {label} label")

        # totals row must have writable cells under the numeric columns
        totals_cells = ots.logical_cells(rows[totals_idx])
        for field in ("workers", "craftsmen", "helpers"):
            idx = cols.get(field)
            if idx is not None and (idx >= len(totals_cells)
                                    or totals_cells[idx] is None):
                fail(f"totals {field} cell",
                     f"writable cell at col{_col_letter(idx)}",
                     "missing/merged slot")

        # details column must still exist (never absorbed into split)
        if cols.get("details") is None:
            fail("details column", "dedicated Details header",
                 "missing (split must not absorb Details)")

        # header row itself must not be merged across data columns
        header_row = rows[header_idx]
        for cell in ots.row_cells(header_row):
            span = int(cell.getAttribute("numbercolumnsspanned") or 1)
            if span > 1 and ots.cell_text(cell):
                fail("header merges",
                     "unmerged data header cells",
                     f"spanned header {ots.cell_text(cell)!r}")
                break

        passed = not failures
        return TemplateReport(name, passed, failures)

    def smoke_fill(self, path: str | Path, out_dir: str | Path,
                   config=None) -> str:
        """Fill a 2-item representative report; returns output path.

        Forces this exact template by pointing all size keys at it
        (public config surface, no privates). Raises with the file
        attached when the template cannot serve a real report.
        """
        from app.libre.filler import TemplateFiller

        cfg = config or self._config
        if cfg is None:
            raise TemplateColumnError(str(path), "no config for smoke fill", [])
        forced = cfg.template.model_copy(update={
            "small_template": str(path), "medium_template": str(path),
            "large_template": str(path)})
        rep = Report(date="2026-09-11", day="Friday", status=ReportStatus.DRAFT,
                     items=[
                         ReportItem(contractor="Smoke A", type="Civil",
                                    zone="Z1", workers=10, craftsmen=7,
                                    helpers=3),
                         ReportItem(contractor="Smoke B", type="Civil",
                                    zone="Z1", workers=4),
                     ])
        return TemplateFiller(cfg.model_copy(update={"template": forced})).fill(
            rep, str(Path(out_dir) / "smoke.ods"))

    def check_all(self, paths: list) -> list[TemplateReport]:
        return [self.check_template(p) for p in paths]
