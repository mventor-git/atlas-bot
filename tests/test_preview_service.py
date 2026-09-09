"""Tests for PDFPreviewService (LibreOffice-native).

Covers:
- generate_preview (fill -> convert -> preview path, pdf_path untouched)
- _generate_doc_path / _generate_preview_path
- cleanup_preview removes both files
"""

import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest

from app.models.database import Report, ReportItem, ReportStatus
from app.services.pdf_preview_service import PDFPreviewService


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def config(tmp_dir: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.preview_folder_path = tmp_dir
    return cfg


@pytest.fixture
def report() -> Report:
    return Report(
        date="2026-07-11", day="Friday", status=ReportStatus.DRAFT,
        items=[ReportItem(contractor="Test Co", workers=5)],
    )


@pytest.fixture
def preview_service(config: MagicMock) -> PDFPreviewService:
    return PDFPreviewService(MagicMock(), MagicMock(), config)


class TestGeneratePreview:
    def test_full_flow_sets_preview_path(self, preview_service: PDFPreviewService, report: Report):
        preview_service._template_filler.fill.return_value = "/tmp/2026-07-11.ods"
        preview_service._pdf_generator.convert_to_pdf.return_value = "/tmp/preview_2026-07-11.pdf"
        result = preview_service.generate_preview(report)
        assert result == "/tmp/preview_2026-07-11.pdf"
        assert report.preview_pdf_path == "/tmp/preview_2026-07-11.pdf"
        assert report.pdf_path is None

    def test_fill_called_with_report(self, preview_service: PDFPreviewService, report: Report):
        preview_service._template_filler.fill.return_value = "/tmp/x.ods"
        preview_service._pdf_generator.convert_to_pdf.return_value = "/tmp/p.pdf"
        preview_service.generate_preview(report)
        args, _ = preview_service._template_filler.fill.call_args
        assert args[0] is report

    def test_convert_called_with_filled_doc(self, preview_service: PDFPreviewService, report: Report):
        preview_service._template_filler.fill.return_value = "/tmp/x.ods"
        preview_service._pdf_generator.convert_to_pdf.return_value = "/tmp/p.pdf"
        preview_service.generate_preview(report)
        args, kwargs = preview_service._pdf_generator.convert_to_pdf.call_args
        assert args[0] == "/tmp/x.ods"


class TestPaths:
    def test_doc_path_uses_preview_folder(self, preview_service: PDFPreviewService, tmp_dir: Path):
        assert preview_service._generate_doc_path("2026-07-11") == str(tmp_dir / "2026-07-11.ods")

    def test_preview_path_has_prefix(self, preview_service: PDFPreviewService, tmp_dir: Path):
        assert preview_service._generate_preview_path("2026-07-11") == str(tmp_dir / "preview_2026-07-11.pdf")

    def test_cleanup_removes_both(self, preview_service: PDFPreviewService, tmp_dir: Path):
        pdf_file = tmp_dir / "preview_2026-07-11.pdf"
        doc_file = tmp_dir / "2026-07-11.ods"
        pdf_file.write_text("pdf")
        doc_file.write_text("doc")
        preview_service.cleanup_preview("2026-07-11")
        assert not pdf_file.exists()
        assert not doc_file.exists()
