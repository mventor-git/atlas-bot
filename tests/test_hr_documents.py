"""HR document rendering tests (ticket-006-D)."""

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from app.libre.hr_fill import fill_hr, queue_for_print, render_pdf, template_for
from app.models.hr import HRRequest, HRRequestStatus, HRRequestType
from app.services.hr_service import HRService


# Minimal 1x1 PNG (receipt stand-in, no imaging dep).
PIXEL_PNG = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
    0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53, 0xDE, 0x00, 0x00, 0x00,
    0x0C, 0x49, 0x44, 0x41, 0x54, 0x08, 0xD7, 0x63, 0xF8, 0xFF, 0xFF, 0x3F,
    0x00, 0x05, 0xFE, 0x02, 0xFE, 0xDC, 0xCC, 0x59, 0xE7, 0x00, 0x00, 0x00,
    0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
])


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def config(temp_dir: Path):
    from app.models.config import AppConfig

    docs = temp_dir / "docs"
    pdfs = temp_dir / "pdf"
    docs.mkdir()
    pdfs.mkdir()
    return AppConfig(
        template={
            "file": "templates/hr-advance-template.ots",
            "tables_file": "database/tables.ods",
            "hr_advance_template": "templates/hr-advance-template.ots",
            "hr_transport_template": "templates/acc-transport-template.ots",
        },
        date={"cell": "B7", "day_cell": "B5"},
        table={"start_row": 11, "columns": {
            "serial": "B", "contractor": "C", "type": "D",
            "zone": "E", "workers": "F", "details": "G"}},
        output={"pdf_folder": str(pdfs), "docs_folder": str(docs)},
        database={"path": ":memory:"},
        tables={"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
    )


def _approved_advance() -> HRRequest:
    return HRRequest(
        id=7, requester_chat_id="u1", requester_name="User One",
        request_type=HRRequestType.ADVANCE, amount=1500, reason="medical",
        site_id="site-a", deduction_month="2026-10",
        status=HRRequestStatus.APPROVED,
        signatures=[
            {"role": "project_manager", "chat_id": "pm1", "name": "PM One",
             "decision": "confirmed", "note": "", "at": "2026-09-08T10:00:00"},
            {"role": "hr", "chat_id": "hr1", "name": "HR One",
             "decision": "approved", "note": "", "at": "2026-09-08T11:00:00"},
        ],
    )


def _texts(path: Path) -> str:
    from odf.opendocument import load
    from app.libre import ots as _ots

    doc = load(str(path))
    table = _ots.first_table(doc)
    return " ".join(
        _ots.cell_text(c)
        for r in table.getElementsByType(_ots.TableRow)
        for c in r.getElementsByType(_ots.TableCell)
    )


class TestTemplatePick:
    def test_en_default(self, config):
        assert template_for("advance", config).name == "hr-advance-template.ots"
        assert template_for("transport", config).name == "acc-transport-template.ots"

    def test_ar_variant(self, config):
        assert template_for("advance", config, lang="ar").name == "hr-advance-template_ar.ots"

    def test_missing_template_raises(self, config, temp_dir: Path):
        from app.models.config import AppConfig

        bad = AppConfig(
            template={"hr_advance_template": "templates/nope.ots",
                      "hr_transport_template": "templates/nope.ots"},
            date={}, table={}, output={}, database={}, tables={},
        )
        with pytest.raises(FileNotFoundError):
            fill_hr(_approved_advance(), bad, str(temp_dir / "x.ods"))


class TestFillAdvance:
    def test_markers_replaced(self, config, temp_dir: Path):
        out = fill_hr(_approved_advance(), config, str(temp_dir / "a.ods"))
        flat = _texts(Path(out))
        assert "[REF]" not in flat and "[AMOUNT]" not in flat
        assert "HR-0007" in flat and "1500" in flat and "medical" in flat
        assert "2026-10" in flat and "PM One" in flat and "HR One" in flat

    def test_ar_variant_fills(self, config, temp_dir: Path):
        out = fill_hr(_approved_advance(), config, str(temp_dir / "a.ods"), lang="ar")
        flat = _texts(Path(out))
        assert "HR-0007" in flat and "[REF]" not in flat


class TestFillTransport:
    def _approved_transport(self, receipt: str) -> HRRequest:
        return HRRequest(
            id=9, requester_chat_id="u1", requester_name="User One",
            request_type=HRRequestType.TRANSPORT, amount=200, reason="site visit",
            site_id="site-a", trip_date="2026-09-01", report_ref="2026-09-01",
            receipt_path=receipt, status=HRRequestStatus.APPROVED,
            signatures=[{"role": "hr", "chat_id": "hr1", "name": "HR One",
                         "decision": "approved", "note": "", "at": "2026-09-08T11:00:00"}],
        )

    def test_receipt_embedded(self, config, temp_dir: Path):
        receipt = temp_dir / "r.png"
        receipt.write_bytes(PIXEL_PNG)
        out = fill_hr(self._approved_transport(str(receipt)), config,
                      str(temp_dir / "t.ods"))
        import zipfile

        names = zipfile.ZipFile(out).namelist()
        assert "Pictures/receipt.jpg" in names
        flat = _texts(Path(out))
        assert "[RECEIPT]" not in flat

    def test_missing_receipt_skipped(self, config, temp_dir: Path):
        out = fill_hr(self._approved_transport(str(temp_dir / "nope.png")),
                      config, str(temp_dir / "t.ods"))
        assert Path(out).exists()


class TestRenderQueue:
    def test_render_pdf(self, config, temp_dir: Path):
        from app.libre.pdf import find_soffice

        try:
            find_soffice()
        except Exception:
            pytest.skip("soffice not available")
        pdf = render_pdf(_approved_advance(), config)
        assert Path(pdf).exists() and Path(pdf).stat().st_size > 1000
        assert Path(pdf).parent == Path(config.pdf_folder_path)

    def test_queue_copies(self, temp_dir: Path, monkeypatch):
        import app.libre.hr_fill as hr_fill

        src = temp_dir / "x.pdf"
        src.write_bytes(b"pdf-bytes")
        monkeypatch.chdir(temp_dir)
        dest = queue_for_print(str(src))
        assert Path(dest).exists()
