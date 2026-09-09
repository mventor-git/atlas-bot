"""Tests for HistoryService (.ods registry).

Covers:
- Creating history file with headers
- Registering a generated report / no-report entry
- Reading history entries
- Appending multiple entries
- Handling non-existent file
"""

import tempfile
from pathlib import Path

import pytest

from app.database.history_service import HistoryService
from app.models.database import Report, ReportStatus


class TestHistoryService:
    """Test suite for HistoryService."""

    @pytest.fixture
    def history_path(self):
        """Create a temporary history file path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "history.ods"

    @pytest.fixture
    def service(self, history_path: Path):
        """Create a HistoryService instance."""
        return HistoryService(str(history_path))

    @pytest.fixture
    def generated_report(self) -> Report:
        """Create a generated report for testing."""
        return Report(
            date="2026-07-11",
            day="Saturday",
            status=ReportStatus.GENERATED,
            telegram_user="user123",
            created_at="2026-07-11T10:00:00",
        )

    @pytest.fixture
    def no_report(self) -> Report:
        """Create a no-report entry for testing."""
        return Report(
            date="2026-07-12",
            day="Sunday",
            status=ReportStatus.NO_REPORT,
            telegram_user="user123",
            created_at="2026-07-12T08:00:00",
        )

    def test_creates_history_file_on_register(self, service: HistoryService, generated_report: Report):
        assert not service.history_path.exists()
        service.register_report(generated_report)
        assert service.history_path.exists()

    def test_register_generated_report(self, service: HistoryService, generated_report: Report):
        service.register_report(generated_report)
        history = service.get_history()
        assert len(history) == 1
        entry = history[0]
        assert entry["Date"] == "2026-07-11"
        assert entry["Day"] == "Saturday"
        assert entry["Status"] == "Generated"
        assert entry["Created At"] == "2026-07-11T10:00:00"

    def test_register_no_report(self, service: HistoryService, no_report: Report):
        service.register_report(no_report)
        history = service.get_history()
        assert len(history) == 1
        assert history[0]["Status"] == "No Report"

    def test_register_multiple_entries(self, service: HistoryService, generated_report: Report, no_report: Report):
        service.register_report(generated_report)
        service.register_report(no_report)
        assert len(service.get_history()) == 2

    def test_register_with_file_paths(self, service: HistoryService, generated_report: Report):
        service.register_report(
            generated_report,
            pdf_path="exports/pdf/2026-07-11.pdf",
            doc_path="exports/docs/2026-07-11.ods",
        )
        history = service.get_history()
        assert len(history) == 1
        assert history[0]["PDF"] == "exports/pdf/2026-07-11.pdf"
        assert history[0]["Document"] == "exports/docs/2026-07-11.ods"

    def test_get_history_empty_when_no_file(self, history_path: Path):
        service = HistoryService(str(history_path))
        assert service.get_history() == []

    def test_history_path_property(self, service: HistoryService, history_path: Path):
        assert service.history_path == history_path.resolve()

    def test_register_same_date_twice_appends(self, service: HistoryService, generated_report: Report):
        service.register_report(generated_report)
        service.register_report(generated_report)
        assert len(service.get_history()) == 2

    def test_get_history_empty_when_no_data_rows(self, service: HistoryService):
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableCell, TableRow
        from odf.text import P

        doc = OpenDocumentSpreadsheet()
        table = Table(name="History")
        row = TableRow()
        for h in ["Date", "Status", "Telegram User", "Created At", "PDF Path", "Document Path"]:
            cell = TableCell()
            p = P()
            p.addText(h)
            cell.addElement(p)
            row.addElement(cell)
        table.addElement(row)
        doc.spreadsheet.addElement(table)
        doc.save(str(service.history_path))

        assert service.get_history() == []

    def test_register_report_with_none_paths(self, service: HistoryService, generated_report: Report):
        report = generated_report
        report.pdf_path = None
        report.excel_path = None
        service.register_report(report)
        row = service.get_history()[0]
        assert row.get("PDF") in ("", None)
        assert row.get("Document") in ("", None)
