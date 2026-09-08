"""
Tests for PDF preview functionality. (mventor-ticket-011)

Covers:
- PDFGenerator.convert_excel_to_pdf (via mocked win32com)
- PDFPreviewService.generate_preview (full flow: Excel fill â†’ PDF conversion)
- PDFPreviewService.cleanup_preview
- Config preview_folder integration
- Report.preview_pdf_path update (not pdf_path)
"""

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import openpyxl
import pytest

from app.excel.template_filler import TemplateFiller
from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus
from app.pdf.generator import PDFGenerator
from app.services.pdf_preview_service import PDFPreviewService
from app.utils.exceptions import PDFError


# ---------------------------------------------------------------------------
# Mock win32com â€” not installed on test systems
# ---------------------------------------------------------------------------

@pytest.fixture
def _mock_win32com():
    """Mock win32com.client module for PDFGenerator tests.

    win32com (pywin32) requires Microsoft Excel and is not installed
    on test/CI systems. We inject real module objects into sys.modules
    so that ``import win32com.client`` resolves correctly through the
    attribute chain (win32com â†’ client â†’ Dispatch).

    NOTE: This is NOT autouse â€” only PDFGenerator tests explicitly
    request this fixture. TemplateFiller tests use real win32com (or
    fall back to openpyxl if unavailable).
    """
    mock_dispatch = MagicMock()
    mock_client = types.ModuleType("win32com.client")
    mock_client.Dispatch = mock_dispatch
    mock_win32com = types.ModuleType("win32com")
    mock_win32com.client = mock_client

    with patch.dict(sys.modules, {"win32com": mock_win32com, "win32com.client": mock_client}):
        yield mock_dispatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides) -> AppConfig:
    """Create a test AppConfig with sensible defaults."""
    defaults = {
        "template": {"file": "t.xlsx", "tables_file": "database/tables.xlsx"},
        "date": {"cell": "B4", "day_cell": "D4"},
        "table": {
            "start_row": 12,
            "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"},
        },
        "output": {
            "pdf_folder": "exports/pdf",
            "excel_folder": "exports/excel",
            "preview_folder": "exports/preview",
        },
        "database": {"path": "test.db"},
        "history": {"file": "h.xlsx"},
        "logging": {"file": "l.log", "level": "INFO", "max_bytes": 1024, "backup_count": 1},
        "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
        "dashboard": {"show_time_remaining": True, "deadline_hour": 14, "deadline_minute": 0},
        "lifecycle": {"auto_lock_hours": 24, "max_versions": 50},
        "validation": {},
        "statistics": {},
        "suggestions": {},
    }
    return AppConfig(**{**defaults, **overrides})


def create_template(path: Path) -> None:
    """Create a minimal Excel template for testing."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Daily Labor Report"
    ws["B4"] = "DATE"
    ws["D4"] = "DAY"
    for col, header in [("A", "#"), ("B", "Contractor"), ("C", "Type"), ("D", "Zone"), ("E", "Workers"), ("F", "Details")]:
        ws[f"{col}11"] = header
    for col in ["A", "B", "C", "D", "E", "F"]:
        ws[f"{col}12"].font = openpyxl.styles.Font(name="Calibri", size=10)
        ws[f"{col}12"].border = openpyxl.styles.Border()
    wb.save(str(path))
    wb.close()


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def template_path(tmp_dir: Path) -> Path:
    path = tmp_dir / "template.xlsx"
    create_template(path)
    return path


@pytest.fixture
def config(template_path: Path) -> AppConfig:
    return make_config(
        template={"file": str(template_path), "tables_file": str(template_path.parent / "tables.xlsx")},
    )


@pytest.fixture
def preview_config(template_path: Path, tmp_dir: Path) -> AppConfig:
    """Config with preview folder pointing into tmp_dir."""
    return make_config(
        template={"file": str(template_path), "tables_file": str(template_path.parent / "tables.xlsx")},
        output={"pdf_folder": str(tmp_dir / "pdf"), "excel_folder": str(tmp_dir / "excel"), "preview_folder": str(tmp_dir)},
    )


@pytest.fixture
def report() -> Report:
    return Report(
        date="2026-07-11",
        day="Ø§Ù„Ø³Ø¨Øª",
        status=ReportStatus.DRAFT,
        telegram_user="test_user",
        items=[
            ReportItem(contractor="Civil Co", type="Main", zone="Zone A", workers=10, details="10 Labor"),
            ReportItem(contractor="Electric Inc", type="Sub", zone="Zone B", workers=5, details="5 Electrician"),
        ],
    )


@pytest.fixture
def template_filler(config: AppConfig) -> TemplateFiller:
    return TemplateFiller(config)


@pytest.fixture
def pdf_generator(config: AppConfig) -> PDFGenerator:
    return PDFGenerator(config)


# ---------------------------------------------------------------------------
# PDFGenerator Tests
# ---------------------------------------------------------------------------

class TestPDFGenerator:
    """Tests for the PDFGenerator class."""

    def test_convert_excel_to_pdf_output_path(self, tmp_dir: Path, pdf_generator: PDFGenerator, _mock_win32com: MagicMock):
        """Should generate PDF at specified output path."""
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)
        output_path = tmp_dir / "output.pdf"
        mock_excel = MagicMock()
        _mock_win32com.return_value = mock_excel
        mock_workbook = MagicMock()
        mock_excel.Workbooks.Open.return_value = mock_workbook

        result = pdf_generator.convert_excel_to_pdf(str(excel_path), output_path=str(output_path))

        assert Path(result).name == "output.pdf"
        _mock_win32com.assert_called_once_with("Excel.Application")
        mock_excel.Workbooks.Open.assert_called_once()
        mock_workbook.ExportAsFixedFormat.assert_called_once_with(0, str(output_path.resolve()))
        mock_workbook.Close.assert_called_once_with(SaveChanges=False)
        mock_excel.Quit.assert_called_once()

    def test_convert_excel_to_pdf_auto_path(self, tmp_dir: Path, pdf_generator: PDFGenerator, _mock_win32com: MagicMock):
        """Should auto-generate PDF path from excel_path when no output_path given."""
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)
        mock_excel = MagicMock()
        _mock_win32com.return_value = mock_excel
        mock_workbook = MagicMock()
        mock_excel.Workbooks.Open.return_value = mock_workbook

        result = pdf_generator.convert_excel_to_pdf(str(excel_path))

        assert "test.pdf" in result

    def test_convert_excel_to_pdf_missing_file(self, pdf_generator: PDFGenerator):
        """Should raise FileNotFoundError if Excel file does not exist."""
        with pytest.raises(FileNotFoundError, match="Excel file not found"):
            pdf_generator.convert_excel_to_pdf("/nonexistent/file.xlsx")

    def test_convert_excel_to_pdf_dispatch_error(self, tmp_dir: Path, pdf_generator: PDFGenerator, _mock_win32com: MagicMock):
        """Should raise PDFError if Excel Dispatch fails."""
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)
        _mock_win32com.side_effect = Exception("Excel not available")

        with pytest.raises(PDFError, match="Failed to convert"):
            pdf_generator.convert_excel_to_pdf(str(excel_path))

    def test_convert_excel_to_pdf_export_error(self, tmp_dir: Path, pdf_generator: PDFGenerator, _mock_win32com: MagicMock):
        """Should raise PDFError if Excel export fails."""
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)
        mock_excel = MagicMock()
        _mock_win32com.return_value = mock_excel
        mock_workbook = MagicMock()
        mock_excel.Workbooks.Open.return_value = mock_workbook
        mock_workbook.ExportAsFixedFormat.side_effect = Exception("Excel error")

        with pytest.raises(PDFError, match="Failed to convert"):
            pdf_generator.convert_excel_to_pdf(str(excel_path))

    def test_convert_excel_to_pdf_creates_output_dir(self, tmp_dir: Path, pdf_generator: PDFGenerator, _mock_win32com: MagicMock):
        """Should create output directory if it doesn't exist."""
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)
        nested_dir = tmp_dir / "nested" / "output"
        output_path = nested_dir / "result.pdf"
        mock_excel = MagicMock()
        _mock_win32com.return_value = mock_excel
        mock_workbook = MagicMock()
        mock_excel.Workbooks.Open.return_value = mock_workbook

        pdf_generator.convert_excel_to_pdf(str(excel_path), output_path=str(output_path))

        assert nested_dir.exists()

    def test_convert_excel_to_pdf_quits_on_error(self, tmp_dir: Path, pdf_generator: PDFGenerator, _mock_win32com: MagicMock):
        """Should attempt to quit Excel even if export fails."""
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)
        mock_excel = MagicMock()
        _mock_win32com.return_value = mock_excel
        mock_excel.Workbooks.Open.side_effect = Exception("Open failed")

        with pytest.raises(PDFError):
            pdf_generator.convert_excel_to_pdf(str(excel_path))

        mock_excel.Quit.assert_called_once()

    def test_convert_excel_to_pdf_quit_not_called_if_excel_fails(self, tmp_dir: Path, pdf_generator: PDFGenerator, _mock_win32com: MagicMock):
        """Should not call Quit if Excel app was never created (import error)."""
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)
        _mock_win32com.side_effect = ImportError("No win32com")

        with pytest.raises(PDFError):
            pdf_generator.convert_excel_to_pdf(str(excel_path))

    def test_pdf_generator_creates_output_folder(self, tmp_dir: Path, _mock_win32com: MagicMock):
        """Should create pdf_folder when auto-generating path."""
        deep_pdf_dir = tmp_dir / "deep" / "pdf"
        gen_config = make_config(
            template={"file": str(tmp_dir / "template.xlsx"), "tables_file": str(tmp_dir / "tables.xlsx")},
            output={"pdf_folder": str(deep_pdf_dir), "excel_folder": str(tmp_dir / "excel"), "preview_folder": str(tmp_dir / "preview")},
        )
        gen = PDFGenerator(gen_config)
        excel_path = tmp_dir / "test.xlsx"
        create_template(excel_path)

        mock_excel = MagicMock()
        _mock_win32com.return_value = mock_excel
        mock_workbook = MagicMock()
        mock_excel.Workbooks.Open.return_value = mock_workbook

        gen.convert_excel_to_pdf(str(excel_path))

        assert deep_pdf_dir.exists()


# ---------------------------------------------------------------------------
# PDFPreviewService Tests
# ---------------------------------------------------------------------------

class TestPDFPreviewService:
    """Tests for PDFPreviewService."""

    @pytest.fixture
    def preview_service(self, template_filler: TemplateFiller, pdf_generator: PDFGenerator, config: AppConfig) -> PDFPreviewService:
        return PDFPreviewService(template_filler, pdf_generator, config)

    @pytest.fixture
    def preview_service_with_tmp(self, tmp_dir: Path, template_path: Path) -> PDFPreviewService:
        """PreviewService whose preview_folder points to a controlled tmp_dir."""
        cfg = make_config(
            template={"file": str(template_path), "tables_file": str(template_path.parent / "tables.xlsx")},
            output={"pdf_folder": str(tmp_dir / "pdf"), "excel_folder": str(tmp_dir / "excel"), "preview_folder": str(tmp_dir)},
        )
        filler = TemplateFiller(cfg)
        gen = PDFGenerator(cfg)
        return PDFPreviewService(filler, gen, cfg)

    def test_generate_preview_returns_path(self, preview_service: PDFPreviewService, report: Report):
        """Should generate a preview PDF and return its path."""
        with patch.object(preview_service._pdf_generator, "convert_excel_to_pdf") as mock_convert:
            mock_convert.return_value = "/exports/preview/preview_2026-07-11.pdf"

            result = preview_service.generate_preview(report)

        assert "preview_2026-07-11.pdf" in result
        mock_convert.assert_called_once()

    def test_generate_preview_does_not_update_pdf_path(self, preview_service: PDFPreviewService, report: Report):
        """Should NOT modify report.pdf_path."""
        report.pdf_path = "/original/report.pdf"

        with patch.object(preview_service._pdf_generator, "convert_excel_to_pdf") as mock_convert:
            mock_convert.return_value = "/exports/preview/preview_2026-07-11.pdf"
            preview_service.generate_preview(report)

        assert report.pdf_path == "/original/report.pdf", "pdf_path should not change"

    def test_generate_preview_updates_preview_pdf_path(self, preview_service: PDFPreviewService, report: Report):
        """Should update report.preview_pdf_path with the generated path."""
        with patch.object(preview_service._pdf_generator, "convert_excel_to_pdf") as mock_convert:
            expected_path = "/exports/preview/preview_2026-07-11.pdf"
            mock_convert.return_value = expected_path
            preview_service.generate_preview(report)

        assert report.preview_pdf_path == expected_path

    def test_generate_preview_calls_template_filler(self, preview_service: PDFPreviewService, report: Report):
        """Should call template_filler.fill with the report."""
        with patch.object(preview_service._template_filler, "fill") as mock_fill:
            mock_fill.return_value = "/tmp/excel.xlsx"
            with patch.object(preview_service._pdf_generator, "convert_excel_to_pdf") as mock_convert:
                mock_convert.return_value = "/exports/preview/preview_2026-07-11.pdf"
                preview_service.generate_preview(report)

            mock_fill.assert_called_once()
            args, kwargs = mock_fill.call_args
            assert args[0] is report

    def test_generate_excel_path_uses_preview_folder(self, preview_service_with_tmp: PDFPreviewService, tmp_dir: Path):
        """Should generate Excel preview file path in preview folder."""
        excel_path = preview_service_with_tmp._generate_excel_path("2026-07-11")
        assert excel_path == str(tmp_dir / "2026-07-11.xlsx")

    def test_generate_preview_path_uses_preview_folder(self, preview_service_with_tmp: PDFPreviewService, tmp_dir: Path):
        """Should generate preview PDF path with preview_ prefix."""
        pdf_path = preview_service_with_tmp._generate_preview_path("2026-07-11")
        assert pdf_path == str(tmp_dir / "preview_2026-07-11.pdf")

    def test_cleanup_preview_removes_files(self, preview_service_with_tmp: PDFPreviewService, tmp_dir: Path):
        """Should delete preview PDF and Excel files."""
        pdf_file = tmp_dir / "preview_2026-07-11.pdf"
        excel_file = tmp_dir / "2026-07-11.xlsx"
        pdf_file.write_text("pdf")
        excel_file.write_text("excel")

        preview_service_with_tmp.cleanup_preview("2026-07-11")

        assert not pdf_file.exists()
        assert not excel_file.exists()

    def test_cleanup_preview_handles_missing_files(self, preview_service_with_tmp: PDFPreviewService, tmp_dir: Path):
        """Should not raise error if preview files don't exist."""
        preview_service_with_tmp.cleanup_preview("2026-07-11")  # Should not raise

    def test_generate_preview_uses_preview_folder_config(self, tmp_dir: Path, template_path: Path, report: Report):
        """Should use preview_folder from config for output."""
        custom_dir = tmp_dir / "custom_preview"
        cfg = make_config(
            template={"file": str(template_path), "tables_file": str(template_path.parent / "tables.xlsx")},
            output={"pdf_folder": str(tmp_dir / "pdf"), "excel_folder": str(tmp_dir / "excel"), "preview_folder": str(custom_dir)},
        )
        filler = TemplateFiller(cfg)
        gen = PDFGenerator(cfg)
        svc = PDFPreviewService(filler, gen, cfg)

        with patch.object(svc._template_filler, "fill") as mock_fill:
            mock_fill.return_value = str(custom_dir / "2026-07-11.xlsx")
            with patch.object(svc._pdf_generator, "convert_excel_to_pdf") as mock_convert:
                mock_convert.return_value = str(custom_dir / "preview_2026-07-11.pdf")
                result = svc.generate_preview(report)

        assert str(custom_dir) in result

    def test_full_flow_with_actual_template(self, preview_service_with_tmp: PDFPreviewService, report: Report, tmp_dir: Path):
        """End-to-end: fill template, mock PDF conversion, verify paths."""
        with patch.object(preview_service_with_tmp._pdf_generator, "convert_excel_to_pdf") as mock_convert:
            expected = str(tmp_dir / "preview_2026-07-11.pdf")
            mock_convert.return_value = expected

            result = preview_service_with_tmp.generate_preview(report)

        assert result == expected
        assert (tmp_dir / "2026-07-11.xlsx").exists()

    def test_full_flow_verifies_excel_content(self, preview_service_with_tmp: PDFPreviewService, report: Report, tmp_dir: Path):
        """Verify the generated Excel file contains report data."""
        with patch.object(preview_service_with_tmp._pdf_generator, "convert_excel_to_pdf") as mock_convert:
            mock_convert.return_value = str(tmp_dir / "preview_2026-07-11.pdf")
            preview_service_with_tmp.generate_preview(report)

        excel_path = tmp_dir / "2026-07-11.xlsx"
        assert excel_path.exists()
        wb = openpyxl.load_workbook(str(excel_path))
        ws = wb.active
        assert ws is not None
        assert ws["B4"].value == "2026-07-11"
        assert ws["D4"].value == "Ø§Ù„Ø³Ø¨Øª"
        assert ws["B12"].value == "Civil Co"
        assert ws["E12"].value == 10
        assert ws["B13"].value == "Electric Inc"
        assert ws["E13"].value == 5
        wb.close()

    def test_generate_preview_raises_on_missing_template(self, preview_service: PDFPreviewService, report: Report):
        """Should propagate FileNotFoundError if template is missing."""
        from unittest.mock import patch
        from app.models.config import AppConfig
        # Mock get_template_for_row_count to return nonexistent path
        with patch("app.excel.template_filler.TemplateFiller._select_template",
                   return_value=Path("/nonexistent/template.xlsx")):
            with pytest.raises(FileNotFoundError):
                preview_service.generate_preview(report)

    def test_cleanup_preview_ignores_other_files(self, preview_service_with_tmp: PDFPreviewService, tmp_dir: Path):
        """Should not delete non-preview files (only preview_*.pdf and date.xlsx)."""
        other_file = tmp_dir / "important.pdf"
        other_file.write_text("keep me")

        preview_service_with_tmp.cleanup_preview("2026-07-11")

        assert other_file.exists()

    def test_preview_folder_created_on_generate(self, report: Report, tmp_dir: Path, template_path: Path):
        """Should create preview folder if it doesn't exist."""
        deep_dir = tmp_dir / "a" / "b" / "c"
        cfg = make_config(
            template={"file": str(template_path), "tables_file": str(template_path.parent / "tables.xlsx")},
            output={"pdf_folder": str(tmp_dir / "pdf"), "excel_folder": str(tmp_dir / "excel"), "preview_folder": str(deep_dir)},
        )
        filler = TemplateFiller(cfg)
        gen = PDFGenerator(cfg)
        svc = PDFPreviewService(filler, gen, cfg)

        with patch.object(svc._template_filler, "fill") as mock_fill:
            mock_fill.return_value = str(deep_dir / "2026-07-11.xlsx")
            with patch.object(svc._pdf_generator, "convert_excel_to_pdf") as mock_convert:
                mock_convert.return_value = str(deep_dir / "preview_2026-07-11.pdf")
                svc.generate_preview(report)

        assert deep_dir.exists()
