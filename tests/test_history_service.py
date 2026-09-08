"""
Tests for HistoryExcelService.

Tests cover:
- Creating history file with headers
- Registering a generated report
- Registering a no-report entry
- Reading history entries
- Appending multiple entries
- Handling non-existent file
- Error handling
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.database.history_service import HistoryExcelService
from app.models.database import Report, ReportStatus


class TestHistoryExcelService:
    """Test suite for HistoryExcelService."""

    @pytest.fixture
    def history_path(self):
        """Create a temporary history file path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "history.xlsx"

    @pytest.fixture
    def service(self, history_path: Path):
        """Create a HistoryExcelService instance."""
        return HistoryExcelService(str(history_path))

    @pytest.fixture
    def generated_report(self) -> Report:
        """Create a generated report for testing."""
        return Report(
            date="2026-07-11",
            day="السبت",
            status=ReportStatus.GENERATED,
            telegram_user="user123",
            created_at="2026-07-11T10:00:00",
        )

    @pytest.fixture
    def no_report(self) -> Report:
        """Create a no-report entry for testing."""
        return Report(
            date="2026-07-12",
            day="الأحد",
            status=ReportStatus.NO_REPORT,
            telegram_user="user123",
            created_at="2026-07-12T08:00:00",
        )

    def test_creates_history_file_on_register(self, service: HistoryExcelService, generated_report: Report):
        """Should create the history file when registering first report."""
        assert not service.history_path.exists(), f"History file should not exist yet"
        service.register_report(generated_report)
        assert service.history_path.exists(), f"History file was not created"

    def test_register_generated_report(self, service: HistoryExcelService, generated_report: Report):
        """Should register a generated report in history."""
        service.register_report(generated_report)
        history = service.get_history()
        assert len(history) == 1, f"Expected 1 entry, got {len(history)}"
        entry = history[0]
        assert entry["Date"] == "2026-07-11", f"Expected '2026-07-11', got '{entry['Date']}'"
        assert entry["Day"] == "السبت", f"Expected 'السبت', got '{entry['Day']}'"
        assert entry["Status"] == "Generated", f"Expected 'Generated', got '{entry['Status']}'"
        assert entry["Created At"] == "2026-07-11T10:00:00", f"Expected '2026-07-11T10:00:00', got '{entry['Created At']}'"

    def test_register_no_report(self, service: HistoryExcelService, no_report: Report):
        """Should register a no-report entry in history."""
        service.register_report(no_report)
        history = service.get_history()
        assert len(history) == 1, f"Expected 1 entry, got {len(history)}"
        assert history[0]["Status"] == "No Report", f"Expected 'No Report', got '{history[0]['Status']}'"

    def test_register_multiple_entries(self, service: HistoryExcelService, generated_report: Report, no_report: Report):
        """Should append multiple history entries."""
        service.register_report(generated_report)
        service.register_report(no_report)

        history = service.get_history()
        assert len(history) == 2, f"Expected 2 entries, got {len(history)}"

    def test_register_with_file_paths(self, service: HistoryExcelService, generated_report: Report):
        """Should register with PDF and Excel paths."""
        service.register_report(
            generated_report,
            pdf_path="exports/pdf/2026-07-11.pdf",
            excel_path="exports/excel/2026-07-11.xlsx",
        )
        history = service.get_history()
        assert len(history) == 1, f"Expected 1 entry, got {len(history)}"
        assert history[0]["PDF"] == "exports/pdf/2026-07-11.pdf", f"Expected PDF path mismatch, got '{history[0]['PDF']}'"
        assert history[0]["Excel"] == "exports/excel/2026-07-11.xlsx", f"Expected Excel path mismatch, got '{history[0]['Excel']}'"

    def test_get_history_empty_when_no_file(self, history_path: Path):
        """Should return empty list when history file doesn't exist."""
        service = HistoryExcelService(str(history_path))
        assert service.get_history() == [], f"Expected empty list, got {service.get_history()}"

    def test_history_path_property(self, service: HistoryExcelService, history_path: Path):
        """Should return the correct history file path."""
        assert service.history_path == history_path.resolve(), f"Expected {history_path.resolve()}, got {service.history_path}"

    def test_register_same_date_twice_appends(self, service: HistoryExcelService, generated_report: Report):
        """Should append duplicate entries (no uniqueness constraint needed)."""
        service.register_report(generated_report)
        service.register_report(generated_report)
        history = service.get_history()
        assert len(history) == 2, f"Expected 2 entries, got {len(history)}"

    def test_register_without_openpyxl_does_not_crash(self, monkeypatch, service: HistoryExcelService, generated_report: Report):
        """Should handle missing openpyxl gracefully."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openpyxl":
                raise ImportError("No module named 'openpyxl'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        # Should not raise
        service.register_report(generated_report)
        # History file should NOT be created
        assert not service.history_path.exists(), f"History file should not exist when openpyxl is missing"

    def test_get_history_empty_when_no_data_rows(self, service: HistoryExcelService):
        """Should return empty list when file has only headers."""
        # Create a minimal history file with only headers
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        headers = ["Date", "Status", "Telegram User", "Created At", "PDF Path", "Excel Path"]
        for i, h in enumerate(headers, 1):
            ws.cell(row=1, column=i, value=h)
        wb.save(service._history_path)
        wb.close()

        history = service.get_history()
        assert history == [], f"Expected empty list, got {len(history)} entries"

    def test_register_report_with_none_paths(self, service: HistoryExcelService, generated_report: Report):
        """Should handle None pdf_path and excel_path."""
        report = generated_report
        report.pdf_path = None
        report.excel_path = None
        service.register_report(report)
        history = service.get_history()
        assert len(history) == 1, f"Expected 1 entry, got {len(history)}"
        # Paths should be empty strings in the Excel file
        row = history[0]
        assert row.get("PDF") == "" or row.get("PDF") is None, f"Expected empty PDF path, got '{row.get('PDF')}'"
        assert row.get("Excel") == "" or row.get("Excel") is None, f"Expected empty Excel path, got '{row.get('Excel')}'"
