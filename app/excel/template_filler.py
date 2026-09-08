"""
Excel Template Filler for Labor-Report.

Fills the company Excel template with report data while preserving
ALL formatting, images, logos, VML drawings, merged cells, and structure.

Uses win32com (Excel COM automation) as the primary backend because
openpyxl does NOT support VML drawings (xl/drawings/vmlDrawing1.vml)
which are used by Excel for image placement. Falls back to openpyxl
if win32com is not available -- note that images WILL be lost in the
openpyxl fallback.

The template is NEVER recreated; only predefined cells are filled.
"""

from copy import copy
from pathlib import Path
from typing import Optional

from app.models.config import AppConfig
from app.models.database import Report, ReportItem
from app.utils.exceptions import ExcelError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ExcelFillError(ExcelError):
    """Raised when template filling fails."""


class TemplateFiller:
    """Fills the Excel template with report data.

    Preserves all template formatting, images, logos, merged cells,
    headers, footers, and page layout. Only predefined cells are
    written to -- the template structure is never modified.

    Uses win32com (Excel COM) for best fidelity, with openpyxl fallback.

    The template has 7 fixed data rows (rows 11-17). If the report has
    more than 7 items, only the first 7 are written. A SUM formula is
    written at F19 =SUM(F11:F17) after filling.

    Usage:
        filler = TemplateFiller(config)
        output_path = filler.fill(report)
    """

    # Row capacities per template size
    TEMPLATE_CAPACITY = {
        "small": 7,    # small_template.xlsx — up to 7 rows
        "medium": 20,  # medium_template.xlsx — up to 20 rows
        "large": 100,  # large_template.xlsx — up to 100 rows
    }

    def __init__(self, config: AppConfig) -> None:
        """Initialize the template filler.

        Args:
            config: Application configuration with template paths
                    and cell mappings.
        """
        self._config = config
        self._start_row = config.table.start_row
        self._columns = config.table.columns
        self._date_cell = config.date_cell
        self._day_cell = config.day_cell
        self._max_data_rows = self.TEMPLATE_CAPACITY["small"]
        self._template_path = Path("templates/small_template.xlsx")

    def _select_template(self, row_count: int) -> Path:
        """Select the appropriate template based on row count.

        Args:
            row_count: Number of contractor rows.

        Returns:
            Path to the selected template.
        """
        return self._config.get_template_for_row_count(row_count)

    def _get_template_capacity(self, template_path: Path) -> int:
        """Get the maximum number of rows for a given template.

        Args:
            template_path: Path to the template file.

        Returns:
            Maximum number of data rows supported.
        """
        path_str = str(template_path)
        if "large" in path_str:
            return self.TEMPLATE_CAPACITY["large"]
        elif "medium" in path_str:
            return self.TEMPLATE_CAPACITY["medium"]
        return self.TEMPLATE_CAPACITY["small"]

    # --- Public API ---

    def fill(self, report: Report, output_path: Optional[str] = None) -> str:
        """Fill the template with report data and save.

        Auto-selects the appropriate template based on row count:
        - small_template: up to 7 rows
        - medium_template: 8-20 rows
        - large_template: 21+ rows

        Args:
            report: The report containing date, day, and items.
            output_path: Optional output path. If None, generates
                        based on report date and config.

        Returns:
            Path to the saved Excel file.

        Raises:
            ExcelFillError: If template loading, filling, or saving fails.
            FileNotFoundError: If the template file does not exist.
        """
        # Auto-select template based on number of items
        row_count = len(report.items) if report.items else 0
        template_path = self._select_template(row_count)
        self._template_path = template_path
        self._max_data_rows = self._get_template_capacity(template_path)

        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Template file not found: {self._template_path}\n"
                f"Please place the template at: {self._template_path}"
            )

        # Generate output path if not provided
        if output_path is None:
            output_path = self._generate_output_path(report.date)

        logger.info("Filling template: %s -> %s (rows=%d, capacity=%d)",
                     self._template_path, output_path, row_count, self._max_data_rows)
        logger.info("Report: date=%s, items=%d", report.date, len(report.items))

        # Try win32com first (preserves VML drawings / images)
        try:
            return self._fill_via_com(report, output_path)
        except ImportError:
            logger.warning(
                "win32com not available. Falling back to openpyxl. "
                "Images/VML drawings will NOT be preserved. "
                "Install pywin32 for full fidelity."
            )
        except Exception as e:
            logger.warning(
                "win32com fill failed (%s). Falling back to openpyxl.", e
            )

        # Fallback: openpyxl (no image support)
        return self._fill_via_openpyxl(report, output_path)

    # --- win32com implementation (preserves images) ---

    def _fill_via_com(self, report: Report, output_path: str) -> str:
        """Fill the template using Excel COM automation (win32com).

        This preserves ALL template features including VML drawings,
        images, charts, and formatting.

        Args:
            report: The report data.
            output_path: Where to save the filled Excel file.

        Returns:
            Path to the saved file.

        Raises:
            ExcelFillError: If filling fails.
        """
        import win32com.client  # type: ignore
        import pythoncom

        pythoncom.CoInitialize()
        excel = None
        wb = None
        success = False

        try:
            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            excel.ScreenUpdating = False
            excel.EnableEvents = False
            excel.Interactive = False
            # Disable all macro/security dialogs (guard against unsupported versions)
            try:
                excel.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
            except Exception:
                logger.warning("Could not set AutomationSecurity (not supported on this Excel version)")

            # Open with UpdateLinks=0 to suppress "Update Links?" dialog
            # ReadOnly=True + IgnoreReadOnlyRecommended=True to suppress read-only prompt
            wb = excel.Workbooks.Open(
                str(self._template_path.resolve()),
                UpdateLinks=0,       # Don't update external links
                ReadOnly=True,       # Open read-only
                IgnoreReadOnlyRecommended=True,  # Don't show "Open as read-only?" prompt
                Notify=False,        # Don't notify about link status
            )
            ws = wb.ActiveSheet

            # Step 1: Fill header (date and day)
            self._fill_header_com(ws, report)

            # Step 2: Fill data table
            if report.items:
                self._fill_table_com(ws, report.items)
            else:
                logger.info("No items to fill. Table left as-is.")

            # Step 3: Ensure output directory exists
            output_path_obj = Path(output_path)
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)

            # Step 4: Remove existing file to avoid overwrite dialogs
            resolved_path = str(output_path_obj.resolve())
            existing = Path(resolved_path)
            if existing.exists():
                try:
                    existing.unlink()
                    logger.debug("Removed existing file: %s", resolved_path)
                except Exception as rm_e:
                    logger.warning("Could not remove existing file %s: %s", resolved_path, rm_e)

            logger.debug("COM SaveAs path: %s", resolved_path)
            # 51 = xlOpenXMLWorkbook (.xlsx), no prompts needed
            wb.SaveAs(resolved_path, FileFormat=51)
            wb.Close(SaveChanges=False)
            excel.Quit()
            success = True
            # At this point the file is saved, workbook closed, Excel quit.
            # Verify file exists (defensive).
            saved = Path(resolved_path)
            if not saved.exists():
                logger.warning("File not found immediately after save: %s", resolved_path)

            # Log AFTER cleanup — no more COM calls after this point.
            logger.info("Template filled via COM and saved: %s", resolved_path)
            return resolved_path

        except Exception as e:
            raise ExcelFillError(
                f"Failed to fill template via COM: {e}",
                original_exception=e,
            )
        finally:
            # Clean up COM resources only if success path didn't already do it.
            if not success:
                self._close_com_resources(wb, excel)
            pythoncom.CoUninitialize()

    def _close_com_resources(self, wb, excel) -> None:
        """Safely close workbook and quit Excel.

        Catches and logs all exceptions during cleanup.
        """
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass

    def _fill_header_com(self, ws, report: Report) -> None:
        """Fill date and day header cells via COM.

        Sets NumberFormat to text ('@') BEFORE writing values to prevent
        Excel from auto-converting dates to serial numbers.

        Args:
            ws: The Excel worksheet object.
            report: The report with date and day values.
        """
        # Write day name (e.g., 'الاثنين') to the configured day cell
        day_cell_ref = ws.Range(self._day_cell)
        day_cell_ref.NumberFormat = "@"
        day_cell_ref.Value = report.day

        # Write date value to the configured date cell
        # Preserve it if it contains a formula (TODAY())
        date_cell_ref = ws.Range(self._date_cell)
        if date_cell_ref.Formula is None or not str(date_cell_ref.Formula).startswith("="):
            # Set text format before writing to prevent date auto-conversion
            date_cell_ref.NumberFormat = "@"
            date_cell_ref.Value = report.date
            logger.debug("Date written to %s: '%s'", self._date_cell, report.date)
        else:
            logger.debug(
                "Date cell %s has formula '%s' -- preserved",
                self._date_cell, date_cell_ref.Formula,
            )

        logger.debug(
            "Header filled via COM: %s='%s' (date), %s='%s' (day)",
            self._date_cell, report.date,
            self._day_cell, report.day,
        )

    def _fill_table_com(self, ws, items: list[ReportItem]) -> None:
        """Fill the labor table with report items via COM.

        Capped at MAX_DATA_ROWS (7). Only the first 7 items are written.
        After filling, writes SUM formula at the total row.

        Args:
            ws: The Excel worksheet object.
            items: List of report items to write.
        """
        start_row = self._start_row
        max_rows = self._max_data_rows
        total_row = start_row + max_rows + 1

        # Cap items at max_rows for the selected template
        capped_items = items[:max_rows]
        needed_rows = len(capped_items)

        logger.debug(
            "Table via COM: start_row=%d, total_items=%d, capped=%d, max_rows=%d",
            start_row, len(items), needed_rows, max_rows,
        )

        # Clear data rows before writing
        for r in range(start_row, start_row + max_rows):
            ws.Range(f"A{r}:G{r}").ClearContents()

        # Write each capped item to rows 11-17
        for i, item in enumerate(capped_items):
            row_num = start_row + i
            self._write_item_row_com(ws, row_num, item, i + 1)

        # Write SUM formula at total row (F19 = SUM(F11:F17))
        sum_formula = f"=SUM(F{start_row}:F{start_row + max_rows - 1})"
        ws.Range(f"F{total_row}").Formula = sum_formula
        logger.debug("SUM formula written to F%d: %s", total_row, sum_formula)

        logger.info("Table filled via COM: %d rows written (max %d)", needed_rows, max_rows)

    def _count_existing_rows_com(self, ws, start_row: int) -> int:
        """Count consecutive non-empty rows below start_row via COM.

        Uses UsedRange as the primary method for accuracy, then falls
        back to scanning individual cells. Handles pythoncom.Empty
        (VT_EMPTY), None, and empty-string cells correctly.

        Args:
            ws: The Excel worksheet object.
            start_row: The first data row.

        Returns:
            Number of existing data rows.
        """
        # Strategy 1: Try UsedRange first (most reliable)
        try:
            used = ws.UsedRange
            if used is not None:
                # Convert COM Count/Row to plain ints
                r = int(used.Row)
                rc = int(used.Rows.Count)
                last_row = r + rc - 1
                logger.debug(
                    "UsedRange: row=%d, rows=%d, last_row=%d, start_row=%d",
                    r, rc, last_row, start_row,
                )
                if last_row < start_row:
                    return 0
                count = max(0, last_row - start_row)
                logger.debug("UsedRange count: %d", count)
                return count
        except Exception as exc:
            logger.debug("UsedRange method failed: %s", exc)

        # Strategy 2: Scan cells (fallback)
        check_col_letter = self._columns.contractor
        row = start_row + 1
        count = 0
        max_check = 500

        while row < start_row + max_check:
            cell = ws.Range(f"{check_col_letter}{row}")
            raw = cell.Value
            # COM empty cells can return pythoncom.Empty, None, or "".
            # Str(None) -> "None" which is truthy, so we MUST check is None first.
            if raw is None:
                logger.debug("Scan: B%d is None -> break at count=%d", row, count)
                break
            val_str = str(raw).strip()
            if val_str:
                count += 1
                row += 1
            else:
                logger.debug("Scan: B%d empty str -> break at count=%d", row, count)
                break

        return count

    def _write_item_row_com(
        self, ws, row_num: int, item: ReportItem, serial: int
    ) -> None:
        """Write a single report item to a row via COM.

        Args:
            ws: The Excel worksheet object.
            row_num: The row number to write to.
            item: The report item data.
            serial: Auto-increment serial number.
        """
        ws.Range(f"{self._columns.serial}{row_num}").Value = serial
        ws.Range(f"{self._columns.contractor}{row_num}").Value = item.contractor
        ws.Range(f"{self._columns.type}{row_num}").Value = item.type if item.type else ""
        ws.Range(f"{self._columns.zone}{row_num}").Value = item.zone if item.zone else ""
        ws.Range(f"{self._columns.workers}{row_num}").Value = item.workers if item.workers is not None else ""
        ws.Range(f"{self._columns.details}{row_num}").Value = item.details if item.details else ""

    # --- openpyxl fallback implementation (NO image support) ---

    def _fill_via_openpyxl(self, report: Report, output_path: str) -> str:
        """Fill the template using openpyxl (fallback, no VML/image support).

        Args:
            report: The report data.
            output_path: Where to save the filled Excel file.

        Returns:
            Path to the saved file.

        Raises:
            ExcelFillError: If filling fails.
        """
        import openpyxl

        try:
            wb = openpyxl.load_workbook(
                filename=str(self._template_path),
                data_only=False,
                keep_vba=False,
            )
            ws = wb.active
            if ws is None:
                raise ExcelFillError("Template workbook has no active sheet.")

            self._fill_header(ws, report)

            if report.items:
                self._fill_table(ws, report.items)
            else:
                logger.info("No items to fill. Table left as-is.")

            output_path_obj = Path(output_path)
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)
            wb.save(str(output_path_obj))
            wb.close()

            logger.info("Template filled via openpyxl and saved: %s", output_path_obj)
            return str(output_path_obj.resolve())

        except Exception as e:
            raise ExcelFillError(
                f"Failed to fill template via openpyxl: {e}",
                original_exception=e,
            )

    def _fill_header(self, ws, report: Report) -> None:
        """Fill the date and day header cells (openpyxl version)."""
        ws[self._day_cell] = report.day
        date_cell = ws[self._date_cell]
        if date_cell.value is None or not isinstance(date_cell.value, str) or not date_cell.value.startswith("="):
            ws[self._date_cell] = report.date
            logger.debug("Date written to %s: '%s'", self._date_cell, report.date)
        else:
            logger.debug(
                "Date cell %s has formula '%s' -- preserved, date value '%s' not written",
                self._date_cell, date_cell.value, report.date,
            )

    def _fill_table(self, ws, items: list[ReportItem]) -> None:
        """Fill the labor table with report items (openpyxl version).

        Capped at MAX_DATA_ROWS (7). Only the first 7 items are written.
        After filling, writes SUM formula at the total row.
        """
        start_row = self._start_row
        max_rows = self._max_data_rows
        total_row = start_row + max_rows + 1
        col_map = {
            self._columns.serial: "serial",
            self._columns.contractor: "contractor",
            self._columns.type: "type",
            self._columns.zone: "zone",
            self._columns.workers: "workers",
            self._columns.details: "details",
        }

        # Cap items at max_rows for the selected template
        capped_items = items[:max_rows]
        needed_rows = len(capped_items)

        logger.debug(
            "Table via openpyxl: start_row=%d, total_items=%d, capped=%d, max_rows=%d",
            start_row, len(items), needed_rows, max_rows,
        )

        data_merges = self._unmerge_data_area(ws, start_row)
        template_styles = self._capture_row_styles(ws, start_row, col_map)

        # Clear existing data rows before writing
        for r in range(start_row, start_row + max_rows):
            for col_key in col_map:
                ws[f"{col_key}{r}"] = None

        # Write each capped item to rows 11-17
        for i, item in enumerate(capped_items):
            row_num = start_row + i
            self._write_item_row(ws, row_num, item, i + 1, col_map, template_styles)

        # Write SUM formula at total row (F19 = SUM(F11:F17))
        sum_formula = f"=SUM(F{start_row}:F{start_row + max_rows - 1})"
        ws[f"F{total_row}"] = sum_formula
        logger.debug("SUM formula written to F%d: %s", total_row, sum_formula)

        logger.info("Table filled via openpyxl: %d rows written (max %d)", needed_rows, max_rows)

    def _capture_row_styles(self, ws, row: int, col_map: dict) -> dict:
        """Capture cell styles from a template row."""
        styles = {}
        for col_letter in col_map:
            cell = ws[f"{col_letter}{row}"]
            styles[col_letter] = {
                "font": copy(cell.font),
                "border": copy(cell.border),
                "fill": copy(cell.fill),
                "alignment": copy(cell.alignment),
                "number_format": cell.number_format,
            }
        return styles

    def _apply_row_styles(self, ws, row: int, col_map: dict, styles: dict) -> None:
        """Apply captured styles to a row."""
        for col_letter in col_map:
            cell = ws[f"{col_letter}{row}"]
            cell_style = styles.get(col_letter)
            if cell_style:
                cell.font = copy(cell_style["font"])
                cell.border = copy(cell_style["border"])
                cell.fill = copy(cell_style["fill"])
                cell.alignment = copy(cell_style["alignment"])
                cell.number_format = cell_style["number_format"]

    def _count_existing_data_rows(self, ws, start_row: int, col_map: dict) -> int:
        """Count consecutive non-empty rows below start_row."""
        check_col = self._columns.contractor
        row = start_row + 1
        count = 0
        max_check = 500
        while row < start_row + max_check:
            cell_value = ws[f"{check_col}{row}"].value
            if cell_value is not None and str(cell_value).strip() != "":
                count += 1
                row += 1
            else:
                break
        return count

    def _unmerge_data_area(self, ws, start_row: int) -> list:
        """Unmerge all merged cells in the data area."""
        unmerged_ranges = []
        ranges_to_unmerge = []
        for merge_range in ws.merged_cells.ranges:
            if merge_range.min_row >= start_row:
                ranges_to_unmerge.append(str(merge_range))
        for merge_str in ranges_to_unmerge:
            try:
                ws.unmerge_cells(merge_str)
                unmerged_ranges.append(merge_str)
            except Exception:
                pass
        return unmerged_ranges

    def _write_item_row(
        self, ws, row_num: int, item: ReportItem, serial: int, col_map: dict, styles: dict
    ) -> None:
        """Write a single report item to a row."""
        self._apply_row_styles(ws, row_num, col_map, styles)
        ws[f"{self._columns.serial}{row_num}"] = serial
        ws[f"{self._columns.contractor}{row_num}"] = item.contractor
        ws[f"{self._columns.type}{row_num}"] = item.type if item.type else ""
        ws[f"{self._columns.zone}{row_num}"] = item.zone if item.zone else ""
        ws[f"{self._columns.workers}{row_num}"] = item.workers if item.workers is not None else ""
        ws[f"{self._columns.details}{row_num}"] = item.details if item.details else ""

    # --- Shared utilities ---

    def _generate_output_path(self, date_str: str) -> str:
        """Generate the output file path for a given date."""
        folder = self._config.excel_folder_path
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / f"{date_str}.xlsx")
