"""Daily v3 acceptance: default fill path cover + totals + 1-page PDF."""

import re
import tempfile
from pathlib import Path

from app.libre.filler import TemplateFiller
from app.models.database import Report, ReportItem, ReportStatus


def _config():
    from app.models.config import AppConfig

    return AppConfig(
        template={
            "file": "templates/contractor-daily-labor-template.ods",
            "tables_file": "database/tables.ods",
            "small_template": "templates/contractor-daily-labor-template.ods",
            "medium_template": "templates/medium_template.ots",
            "large_template": "templates/large_template.ots",
            "empty_day_template": "templates/empty-day.ots",
            "contractor_report_template": "templates/contractor_report_template.ots",
        },
        date={"cell": "B7", "day_cell": "B5"},
        table={"start_row": 11, "columns": {
            "serial": "B", "contractor": "C", "type": "D",
            "zone": "E", "workers": "F", "details": "G",
        }},
        output={"pdf_folder": "exports/pdf", "docs_folder": "exports/docs"},
        database={"path": ":memory:"},
        tables={"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
    )


def _texts(path: Path):
    from odf.opendocument import load
    from app.libre import ots as _ots

    doc = load(str(path))
    table = _ots.first_table(doc)
    return [
        [_ots.cell_text(c) for c in _ots.logical_cells(r)]
        for r in table.getElementsByType(_ots.TableRow)
    ]


def test_v3_cover_totals_one_page():
    config = _config()
    rep = Report(
        date="2026-09-08", day="Monday", status=ReportStatus.DRAFT,
        items=[
            ReportItem(contractor="Co 0", type="Civil", zone="A",
                       workers=1, details="d0"),
            ReportItem(contractor="Co 1", type="Civil", zone="A",
                       workers=2, details="d1"),
        ],
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out = Path(TemplateFiller(config).fill(rep, str(tmp / "v3.ods")))
        grid = _texts(out)
        flat = " | ".join(" ".join(r) for r in grid)
        assert "Daily Labor Report" in flat
        for marker in ("Company", "Site", "Date", "Automated By"):
            assert marker in flat
        assert "Co 0" in flat and "Co 1" in flat
        (tot,) = [r for r in grid if "Total" in " ".join(r)]
        assert tot[4] == "3"
        from app.libre.pdf import PDFGenerator, find_soffice

        try:
            find_soffice()
        except Exception:
            print("soffice absent, convert assert skipped")
            return
        pdf = Path(PDFGenerator(config).convert_to_pdf(
            str(out), str(tmp / "v3.pdf")))
        assert pdf.exists() and pdf.stat().st_size > 1000
        pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))
        assert pages == 1
