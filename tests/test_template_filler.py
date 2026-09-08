"""
Tests for the Excel Template Filler service.

Tests cover:
- Filling header cells (day in Arabic, date with TODAY formula preservation)
- Filling table with report items (columns B-G)
- Dynamic row insertion (more items than template rows)
- Merged cell handling (B5:C5, B7:C7)
- Empty items list / no-report status
- Error handling (missing template, invalid data)
- Output path generation
- Formula preservation in date cell
- Multiple report configurations
"""

import os
import tempfile
from copy import copy
from pathlib import Path
from typing import Generator

import openpyxl
import pytest
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.excel.template_filler import TemplateFiller, ExcelFillError
from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus


def create_test_template(file_path: Path, data_rows: int = 3) -> None:
    """Create a minimal Excel template for testing that matches the actual template layout.

    Layout matches 'Template.xlsx':
    - B5 (merged C5) = Day name
    - B7 (merged C7) = Date (may have TODAY formula)
    - Row 10 = Table headers (B=م, C=اسم المقاول, D=البند, E=مكان العمل, F=عدد العمال, G=العدد التفصيلي)
    - Row 11 = First data row
    - Column A is unusable

    Args:
        file_path: Where to save the template.
        data_rows: Number of pre-formatted data rows (default 3).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # --- Header area ---
    # Merged title cell (B1:G1 since A is unusable)
    ws.merge_cells("B1:G1")
    ws["B1"] = "DAILY LABOR REPORT"
    ws["B1"].font = Font(name="Aptos Narrow", size=16, bold=True)
    ws["B1"].alignment = Alignment(horizontal="center", vertical="center")

    # Day label
    ws["A4"] = "Day:"
    ws["A4"].font = Font(bold=True)

    # Day value cell — B5 merged with C5
    ws.merge_cells("B5:C5")
    ws["B5"] = ""
    ws["B5"].font = Font(name="Aptos Narrow", size=11)
    ws["B5"].alignment = Alignment(horizontal="center", vertical="center")

    # Date value cell — B7 merged with C7 (may have TODAY formula)
    ws.merge_cells("B7:C7")
    ws["B7"] = ""
    ws["B7"].font = Font(name="Aptos Narrow", size=11)
    ws["B7"].alignment = Alignment(horizontal="center", vertical="center")
    ws["B7"].number_format = "YYYY-MM-DD"

    # --- Table header (row 10) columns B-G ---
    headers = ["م", "اسم المقاول", "البند", "مكان العمل", "عدد العمال", "العدد التفصيلي"]
    header_font = Font(name="Aptos Narrow", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for col_idx, header in enumerate(headers, 2):  # Start at column B (index 2)
        cell = ws.cell(row=10, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # --- Data rows (row 11 onwards) columns B-G ---
    data_font = Font(name="Aptos Narrow", size=10)
    data_alignment = Alignment(horizontal="center", vertical="center")
    even_fill = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")

    for row_offset in range(data_rows):
        row_num = 11 + row_offset
        for col_idx in range(2, 8):  # Columns B through G (2-7)
            cell = ws.cell(row=row_num, column=col_idx)
            cell.font = data_font
            cell.alignment = data_alignment
            cell.border = thin_border
            if row_offset % 2 == 0:
                cell.fill = even_fill

    # --- Total row (row 19, matching actual template layout) ---
    total_font = Font(name="Aptos Narrow", size=11, bold=True)
    total_alignment = Alignment(horizontal="right", vertical="center")
    ws.merge_cells("C19:E19")
    ws["C19"] = "الإجمالي :"
    ws["C19"].font = total_font
    ws["C19"].alignment = total_alignment
    # F19 will be filled by TemplateFiller with SUM formula
    ws["F19"].font = total_font
    ws["F19"].alignment = total_alignment
    ws["F19"].number_format = "0"

    # Set column widths (matches actual template)
    col_widths = {"A": 11.7, "B": 7.0, "C": 19.3, "D": 10.9, "E": 15.6, "F": 23.0, "G": 31.7}
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    wb.save(str(file_path))
    wb.close()


class TestTemplateFiller:
    """Test suite for TemplateFiller."""

    @pytest.fixture
    def temp_dir(self) -> Generator[Path, None, None]:
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir)

    @pytest.fixture
    def template_path(self, temp_dir: Path) -> Path:
        """Create a test template file."""
        path = temp_dir / "Daily Labor Report.xlsx"
        create_test_template(path, data_rows=3)
        return path

    @pytest.fixture
    def config(self, template_path: Path, temp_dir: Path) -> AppConfig:
        """Create an AppConfig pointing to the test template using the new layout."""
        return AppConfig(
            template={
                "file": str(template_path),
                "tables_file": str(temp_dir / "tables.xlsx"),
                "small_template": str(template_path),
                "empty_day_template": str(template_path),
            },
            date={"cell": "B7", "day_cell": "B5"},
            table={
                "start_row": 11,
                "columns": {
                    "serial": "B",
                    "contractor": "C",
                    "type": "D",
                    "zone": "E",
                    "workers": "F",
                    "details": "G",
                },
            },
            output={
                "pdf_folder": str(temp_dir / "exports" / "pdf"),
                "excel_folder": str(temp_dir / "exports" / "excel"),
            },
            database={"path": str(temp_dir / "test.db")},
            logging={"file": str(temp_dir / "logs" / "test.log"), "level": "DEBUG"},
        )

    @pytest.fixture
    def filler(self, config: AppConfig) -> TemplateFiller:
        """Create a TemplateFiller instance."""
        return TemplateFiller(config)

    @pytest.fixture
    def sample_report(self) -> Report:
        """Create a sample report with items."""
        report = Report(
            date="2026-07-11",
            day="السبت",
            status=ReportStatus.GENERATED,
        )
        report.add_item(ReportItem(
            contractor="Civil Contractor",
            type="Civil",
            zone="Zone A",
            workers=15,
            details="10 Mason, 3 Helper, 2 Carpenter",
        ))
        report.add_item(ReportItem(
            contractor="Electrical Contractor",
            type="Electrical",
            zone="Zone B",
            workers=8,
            details="5 Electrician, 3 Helper",
        ))
        report.add_item(ReportItem(
            contractor="Mechanical Contractor",
            type="Mechanical",
            zone="",
            workers=6,
            details="4 Technician, 2 Helper",
        ))
        return report

    # --- Header Tests ---

    def test_fill_day_cell(self, filler: TemplateFiller, sample_report: Report):
        """Should fill the day cell (B5) with the Arabic day name."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active
        assert ws["B5"].value == "السبت", f"Expected 'السبت', got '{ws['B5'].value}'"
        wb.close()

    def test_fill_date_cell(self, filler: TemplateFiller, sample_report: Report):
        """Should fill the date cell (B7) when no formula is present."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active
        assert ws["B7"].value == "2026-07-11", f"Expected '2026-07-11', got '{ws['B7'].value}'"
        wb.close()

    def test_preserves_formula_in_date_cell(self, filler: TemplateFiller, temp_dir: Path):
        """Should NOT overwrite the date cell if it contains a formula (=TODAY())."""
        # Create a template with a TODAY() formula in B7
        template_path = temp_dir / "formula_template.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.merge_cells("B7:C7")
        ws["B7"] = "=TODAY()"
        ws["B7"].number_format = "YYYY-MM-DD"
        # Add minimal table header
        ws["B10"] = "م"
        wb.save(str(template_path))
        wb.close()

        formula_config = AppConfig(
            template={"file": str(template_path), "tables_file": str(temp_dir / "tables.xlsx"),
                      "small_template": str(template_path), "empty_day_template": str(template_path)},
            date={"cell": "B7", "day_cell": "B5"},
            table={"start_row": 11, "columns": {
                "serial": "B", "contractor": "C", "type": "D",
                "zone": "E", "workers": "F", "details": "G",
            }},
            output={"pdf_folder": str(temp_dir / "exports" / "pdf"), "excel_folder": str(temp_dir / "exports" / "excel")},
            database={"path": str(temp_dir / "test.db")},
        )
        formula_filler = TemplateFiller(formula_config)

        report = Report(date="2026-07-11", day="السبت")
        output = formula_filler.fill(report)

        wb2 = openpyxl.load_workbook(output)
        ws2 = wb2.active
        # The formula should still be there, NOT the date value
        assert ws2["B7"].value == "=TODAY()", (
            f"Expected formula '=TODAY()' preserved, got '{ws2['B7'].value}'"
        )
        wb2.close()

    def test_fill_header_no_items(self, filler: TemplateFiller):
        """Should fill header even when there are no items."""
        report = Report(date="2026-07-11", day="السبت", status=ReportStatus.GENERATED)
        output = filler.fill(report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active
        assert ws["B5"].value == "السبت", f"Expected 'السبت' in day cell, got '{ws['B5'].value}'"
        assert ws["B7"].value == "2026-07-11", f"Expected '2026-07-11' in date cell, got '{ws['B7'].value}'"
        wb.close()

    def test_fill_header_no_report_status(self, filler: TemplateFiller):
        """Should fill header for no_report status."""
        report = Report(date="2026-07-11", day="السبت", status=ReportStatus.NO_REPORT)
        output = filler.fill(report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active
        assert ws["B5"].value == "السبت", f"Expected 'السبت' in day cell, got '{ws['B5'].value}'"
        assert ws["B7"].value == "2026-07-11", f"Expected '2026-07-11' in date cell, got '{ws['B7'].value}'"
        wb.close()

    # --- Table Fill Tests ---

    def test_fill_table_with_items(self, filler: TemplateFiller, sample_report: Report):
        """Should fill all items into the table (columns B-G starting row 11)."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Check values for first item (row 11: B=serial, C=contractor, D=type, E=zone, F=workers, G=details)
        assert ws["B11"].value == 1, f"Expected serial 1, got '{ws['B11'].value}'"
        assert ws["C11"].value == "Civil Contractor", f"Expected 'Civil Contractor', got '{ws['C11'].value}'"
        assert ws["D11"].value == "Civil", f"Expected 'Civil', got '{ws['D11'].value}'"
        assert ws["E11"].value == "Zone A", f"Expected 'Zone A', got '{ws['E11'].value}'"
        assert ws["F11"].value == 15, f"Expected 15 workers, got {ws['F11'].value}"
        assert ws["G11"].value == "10 Mason, 3 Helper, 2 Carpenter", f"Expected details, got '{ws['G11'].value}'"

        # Check values for second item (row 12)
        assert ws["B12"].value == 2, f"Expected serial 2, got '{ws['B12'].value}'"
        assert ws["F12"].value == 8, f"Expected 8 workers, got {ws['F12'].value}"

        # Check values for third item (row 13)
        assert ws["B13"].value == 3, f"Expected serial 3, got '{ws['B13'].value}'"
        assert ws["C13"].value == "Mechanical Contractor", f"Expected 'Mechanical Contractor', got '{ws['C13'].value}'"

        wb.close()

    def test_fill_more_items_than_template_rows(self, filler: TemplateFiller, sample_report: Report, temp_dir: Path):
        """Should insert rows when more items exist than template rows."""
        # Create template with only 1 data row
        template_path = temp_dir / "small_template.xlsx"
        create_test_template(template_path, data_rows=1)
        config = AppConfig(
            template={"file": str(template_path), "tables_file": str(temp_dir / "tables.xlsx")},
            date={"cell": "B7", "day_cell": "B5"},
            table={"start_row": 11, "columns": {
                "serial": "B", "contractor": "C", "type": "D",
                "zone": "E", "workers": "F", "details": "G",
            }},
            output={"pdf_folder": str(temp_dir / "exports" / "pdf"), "excel_folder": str(temp_dir / "exports" / "excel")},
            database={"path": str(temp_dir / "test.db")},
        )
        small_filler = TemplateFiller(config)

        # Report with 5 items (template only has 1 row)
        report = Report(date="2026-07-11", day="السبت")
        for i in range(5):
            report.add_item(ReportItem(contractor=f"Contractor {i+1}", workers=5+i))

        output = small_filler.fill(report)

        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Should have 5 rows of data starting at row 11
        assert ws["B11"].value == 1, f"Expected serial 1, got '{ws['B11'].value}'"
        assert ws["C11"].value == "Contractor 1", f"Expected 'Contractor 1', got '{ws['C11'].value}'"
        assert ws["B12"].value == 2, f"Expected serial 2, got '{ws['B12'].value}'"
        assert ws["B15"].value == 5, f"Expected serial 5, got '{ws['B15'].value}'"
        assert ws["C15"].value == "Contractor 5", f"Expected 'Contractor 5', got '{ws['C15'].value}'"

        wb.close()

    def test_fill_fewer_items_than_template_rows(self, filler: TemplateFiller, sample_report: Report):
        """Should handle having fewer items than template rows."""
        # Only take 1 item from sample
        report = Report(date="2026-07-11", day="السبت")
        report.add_item(sample_report.items[0])

        output = filler.fill(report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Only 1 data row at row 11
        assert ws["B11"].value == 1, f"Expected serial 1, got '{ws['B11'].value}'"
        assert ws["C11"].value == "Civil Contractor", f"Expected 'Civil Contractor', got '{ws['C11'].value}'"

        wb.close()

    def test_fill_with_workers_zero(self, filler: TemplateFiller):
        """Should handle workers=0."""
        report = Report(date="2026-07-11", day="السبت")
        report.add_item(ReportItem(contractor="Test Co", workers=0))
        output = filler.fill(report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active
        assert ws["F11"].value == 0, f"Expected 0 workers, got {ws['F11'].value}"
        wb.close()

    def test_fill_without_data_rows_in_template(self, temp_dir: Path):
        """Should handle template with zero data rows."""
        template_path = temp_dir / "no_data_rows.xlsx"
        create_test_template(template_path, data_rows=0)
        config = AppConfig(
            template={"file": str(template_path), "tables_file": str(temp_dir / "tables.xlsx")},
            date={"cell": "B7", "day_cell": "B5"},
            table={"start_row": 11, "columns": {
                "serial": "B", "contractor": "C", "type": "D",
                "zone": "E", "workers": "F", "details": "G",
            }},
            output={"pdf_folder": str(temp_dir / "exports" / "pdf"), "excel_folder": str(temp_dir / "exports" / "excel")},
            database={"path": str(temp_dir / "test.db")},
        )
        filler = TemplateFiller(config)
        report = Report(date="2026-07-11", day="السبت")
        report.add_item(ReportItem(contractor="Only One"))
        output = filler.fill(report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active
        assert ws["C11"].value == "Only One", f"Expected 'Only One', got {ws['C11'].value}"
        wb.close()

    # --- Formatting Preservation ---

    def test_preserves_template_font(self, filler: TemplateFiller, sample_report: Report):
        """Should preserve font from template row."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Font should match template (Aptos Narrow, 10)
        font = ws["C11"].font
        assert font.name == "Aptos Narrow", f"Expected font name 'Aptos Narrow', got '{font.name}'"
        assert font.size == 10, f"Expected font size 10, got {font.size}"

        # Header font should be preserved
        header_font = ws["B10"].font
        assert header_font.bold is True, "Expected header font to be bold"

        wb.close()

    def test_preserves_alignment(self, filler: TemplateFiller, sample_report: Report):
        """Should preserve cell alignment from template."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        alignment = ws["C11"].alignment
        assert alignment.horizontal == "center", f"Expected horizontal center, got '{alignment.horizontal}'"
        assert alignment.vertical == "center", f"Expected vertical center, got '{alignment.vertical}'"

        wb.close()

    def test_preserves_borders(self, filler: TemplateFiller, sample_report: Report):
        """Should preserve cell borders from template."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        border = ws["C11"].border
        assert border.left.style == "thin", f"Expected thin left border, got '{border.left.style}'"
        assert border.right.style == "thin", f"Expected thin right border, got '{border.right.style}'"
        assert border.top.style == "thin", f"Expected thin top border, got '{border.top.style}'"
        assert border.bottom.style == "thin", f"Expected thin bottom border, got '{border.bottom.style}'"

        wb.close()

    def test_preserves_header_formatting(self, filler: TemplateFiller, sample_report: Report):
        """Should preserve table header formatting."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Header row (10) must be untouched
        assert ws["B10"].value == "م", f"Expected header 'م', got '{ws['B10'].value}'"
        header_fill = ws["B10"].fill
        assert header_fill.start_color is not None, "Expected header fill to have a start color"

        wb.close()

    def test_preserves_merged_header_cells(self, filler: TemplateFiller, sample_report: Report):
        """Should preserve merged cells B5:C5 and B7:C7 outside the data area."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        merged = [str(m) for m in ws.merged_cells.ranges]

        # B5:C5 merge should still exist (day cell)
        assert "B5:C5" in merged, f"B5:C5 merged cell was lost. Merged: {merged}"

        # B7:C7 merge should still exist (date cell)
        assert "B7:C7" in merged, f"B7:C7 merged cell was lost. Merged: {merged}"

        # Title merge B1:G1 should still exist
        assert "B1:G1" in merged, f"B1:G1 merged title was lost. Merged: {merged}"

        wb.close()

    def test_preserves_merged_title(self, filler: TemplateFiller, sample_report: Report):
        """Should preserve merged title cell."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Title value should be preserved
        assert ws["B1"].value == "DAILY LABOR REPORT", f"Expected 'DAILY LABOR REPORT', got '{ws['B1'].value}'"

        wb.close()

    # --- Edge Cases ---

    def test_missing_template_file(self, temp_dir: Path):
        """Should raise FileNotFoundError for missing template."""
        config = AppConfig(
            template={"file": str(temp_dir / "nonexistent.xlsx"), "tables_file": str(temp_dir / "tables.xlsx"),
                      "small_template": str(temp_dir / "nonexistent.xlsx"),
                      "empty_day_template": str(temp_dir / "nonexistent.xlsx")},
            date={"cell": "B7", "day_cell": "B5"},
            table={"start_row": 11, "columns": {
                "serial": "B", "contractor": "C", "type": "D",
                "zone": "E", "workers": "F", "details": "G",
            }},
            output={"pdf_folder": str(temp_dir / "exports" / "pdf"), "excel_folder": str(temp_dir / "exports" / "excel")},
        )
        filler = TemplateFiller(config)
        report = Report(date="2026-07-11", day="السبت")

        with pytest.raises(FileNotFoundError, match="Template file not found"):
            filler.fill(report)

    def test_custom_output_path(self, filler: TemplateFiller, sample_report: Report, temp_dir: Path):
        """Should accept a custom output path."""
        custom_path = str(temp_dir / "custom" / "my_report.xlsx")
        result = filler.fill(sample_report, output_path=custom_path)
        assert Path(result).exists(), f"Expected output file to exist at {result}"
        assert Path(result).resolve() == Path(custom_path).resolve(), (
            f"Expected {Path(result).resolve()} to match {Path(custom_path).resolve()}"
        )

    def test_output_path_generation(self, filler: TemplateFiller, sample_report: Report):
        """Should generate output path based on date."""
        output = filler.fill(sample_report)
        path = Path(output)
        assert path.name == "2026-07-11.xlsx", f"Expected filename '2026-07-11.xlsx', got '{path.name}'"
        assert path.parent.name == "excel", f"Expected parent dir 'excel', got '{path.parent.name}'"

    def test_item_with_nullable_fields(self, filler: TemplateFiller):
        """Should handle items with empty/null optional fields."""
        report = Report(date="2026-07-11", day="السبت")
        report.add_item(ReportItem(
            contractor="Test Contractor",
            type=None,
            zone=None,
            workers=None,
            details=None,
        ))

        output = filler.fill(report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Column C = contractor name
        assert ws["C11"].value == "Test Contractor", f"Expected 'Test Contractor', got '{ws['C11'].value}'"
        # openpyxl stores empty strings as None
        assert ws["D11"].value is None or ws["D11"].value == "", f"Expected empty type, got '{ws['D11'].value}'"
        assert ws["E11"].value is None or ws["E11"].value == "", f"Expected empty zone, got '{ws['E11'].value}'"
        assert ws["F11"].value is None or ws["F11"].value == "", f"Expected empty workers, got '{ws['F11'].value}'"
        assert ws["G11"].value is None or ws["G11"].value == "", f"Expected empty details, got '{ws['G11'].value}'"

        wb.close()

    def test_serial_numbers_auto_increment(self, filler: TemplateFiller, sample_report: Report):
        """Should auto-increment serial numbers."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        assert ws["B11"].value == 1, f"Expected serial 1, got '{ws['B11'].value}'"
        assert ws["B12"].value == 2, f"Expected serial 2, got '{ws['B12'].value}'"
        assert ws["B13"].value == 3, f"Expected serial 3, got '{ws['B13'].value}'"

        wb.close()

    def test_column_a_is_unused(self, filler: TemplateFiller, sample_report: Report):
        """Should leave column A entirely untouched."""
        output = filler.fill(sample_report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # Check that column A is empty across data rows
        for row in range(10, 20):
            assert ws[f"A{row}"].value is None or ws[f"A{row}"].value == "", (
                f"Column A row {row} should be untouched, got '{ws[f'A{row}'].value}'"
            )

        wb.close()

    # --- Large Dataset (capped at MAX_DATA_ROWS=7) ---

    def test_many_items_use_large_template(self, filler: TemplateFiller, temp_dir: Path):
        """Should auto-select large template when many items exist (multi-template system)."""
        template_path = temp_dir / "large_template.xlsx"
        create_test_template(template_path, data_rows=5)
        config = AppConfig(
            template={"file": str(template_path), "tables_file": str(temp_dir / "tables.xlsx"),
                      "small_template": str(template_path), "medium_template": str(template_path),
                      "large_template": str(template_path)},
            date={"cell": "B7", "day_cell": "B5"},
            table={"start_row": 11, "columns": {
                "serial": "B", "contractor": "C", "type": "D",
                "zone": "E", "workers": "F", "details": "G",
            }},
            output={"pdf_folder": str(temp_dir / "exports" / "pdf"), "excel_folder": str(temp_dir / "exports" / "excel")},
        )
        large_filler = TemplateFiller(config)

        report = Report(date="2026-07-11", day="السبت")
        for i in range(50):
            report.add_item(ReportItem(contractor=f"Contractor {i+1}", workers=i+10))

        output = large_filler.fill(report)
        wb = openpyxl.load_workbook(output)
        ws = wb.active

        # All 50 items should be written (large template capacity = 100)
        assert ws["B11"].value == 1, f"Expected serial 1, got '{ws['B11'].value}'"
        assert ws["C11"].value == "Contractor 1", f"Expected 'Contractor 1', got '{ws['C11'].value}'"
        assert ws["C60"].value == "Contractor 50", f"Expected 'Contractor 50' at row 60, got '{ws['C60'].value}'"

        # F19 is no longer the total row with large template; capacity-based total row differs
        wb.close()
