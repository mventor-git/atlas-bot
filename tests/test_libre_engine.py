"""Tests for the LibreOffice-native document engine.

Covers (against the real shipped templates):
- Daily fill: day/date markers, item rows, serials, totals, overflow cloning
- Empty reports use the empty-day template
- Contractor period reports: info line, entries, summary
- Tables master read/search on .ods
- History register/read on .ods
- PDF rendering via headless soffice (skipped when soffice is absent)
"""

import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from app.libre.contractor_report import ContractorReportFiller
from app.libre.filler import TemplateFiller
from app.libre.tables import TablesReaderService
from app.models.database import Report, ReportItem, ReportStatus


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def config():
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


def _report(n: int) -> Report:
    return Report(
        date="2026-09-08", day="Monday", status=ReportStatus.DRAFT,
        items=[
            ReportItem(contractor=f"Co {i}", type="Civil", zone="A",
                       workers=i + 1, details=f"d{i}")
            for i in range(n)
        ],
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


class TestDailyFill:
    def test_markers_items_totals(self, config, temp_dir: Path):
        out = Path(TemplateFiller(config).fill(_report(2), str(temp_dir / "r.ods")))
        assert out.exists()
        grid = _texts(out)
        flat = " | ".join(" ".join(r) for r in grid)
        assert "Monday" in flat and "2026-09-08" in flat
        assert "Co 0" in flat and "Co 1" in flat
        assert "3" in grid[13][5] or "3" in " ".join(grid[13])

    def test_overflow_clones_rows(self, config, temp_dir: Path):
        out = Path(TemplateFiller(config).fill(_report(6), str(temp_dir / "r.ods")))
        grid = _texts(out)
        flat = " | ".join(" ".join(r) for r in grid)
        assert "Co 5" in flat
        assert "21" in flat  # 1+2+3+4+5+6

    def test_empty_report_uses_empty_day(self, config, temp_dir: Path):
        rep = Report(date="2026-09-08", day="Monday",
                     status=ReportStatus.NO_REPORT, items=[])
        out = Path(TemplateFiller(config).fill(rep, str(temp_dir / "e.ods")))
        assert out.exists()

    def test_missing_template_raises(self, config, temp_dir: Path):
        from app.models.config import AppConfig

        bad = config.model_copy(update={"template": config.template.model_copy(
            update={"small_template": "templates/nope.ots"})})
        with pytest.raises(FileNotFoundError):
            TemplateFiller(bad).fill(_report(1), str(temp_dir / "x.ods"))


class TestSplitRender:
    """023: craftsmen+helpers print in the detailed-number column (G)."""

    def _row_for(self, grid, contractor):
        (row,) = [r for r in grid if len(r) > 2 and r[2] == contractor]
        return row

    def test_split_plus_derived_and_manual_wins(self, config, temp_dir: Path):
        rep = Report(
            date="2026-09-11", day="Friday", status=ReportStatus.DRAFT,
            items=[
                ReportItem(contractor="SplitCo", workers=10,
                           craftsmen=7, helpers=3),
                ReportItem(contractor="DerivedCo", workers=10, craftsmen=10),
                ReportItem(contractor="ManualCo", workers=3, craftsmen=1,
                           helpers=2, details="10 Mason, 3 Helper"),
            ])
        out = Path(TemplateFiller(config).fill(rep, str(temp_dir / "s.ods")))
        grid = _texts(out)
        assert self._row_for(grid, "SplitCo")[6] == "7+3"
        assert self._row_for(grid, "DerivedCo")[6] == "10+0"
        assert self._row_for(grid, "ManualCo")[6] == "10 Mason, 3 Helper"

    def test_legacy_item_prints_empty(self, config, temp_dir: Path):
        rep = Report(
            date="2026-09-11", day="Friday", status=ReportStatus.DRAFT,
            items=[ReportItem(contractor="OldCo", workers=4)])
        out = Path(TemplateFiller(config).fill(rep, str(temp_dir / "l.ods")))
        assert self._row_for(_texts(out), "OldCo")[6] == ""


def _relabel_header(src: str, dst: str, updates: dict):
    """Copy a shipped template with header cell renames (024 fixture)."""
    from app.libre import ots as _ots

    doc = _ots.load_doc(src)
    table = _ots.first_table(doc)
    h = _ots.find_first(table, ("Contractor",))
    cells = _ots.logical_cells(
        table.getElementsByType(_ots.TableRow)[h])
    for idx, text in updates.items():
        _ots.set_cell_text(cells[idx], text)
    _ots.save(doc, dst)


SMALL = "templates/contractor-daily-labor-template.ods"


class TestDedicatedColumns:
    """024: header-driven columns; owner-inserted Craftsmen/Helpers."""

    def test_split_columns_fill_and_total(self, config, temp_dir: Path):
        tpl = temp_dir / "split.ods"
        _relabel_header(SMALL, str(tpl),
                        {7: "Craftsmen", 8: "Helpers"})
        cfg = config.model_copy(update={"template": config.template.model_copy(
            update={"small_template": str(tpl)})})
        rep = Report(
            date="2026-09-11", day="Friday", status=ReportStatus.DRAFT,
            items=[
                ReportItem(contractor="A Co", workers=10, craftsmen=7,
                           helpers=3),
                ReportItem(contractor="B Co", workers=7, craftsmen=2),
                ReportItem(contractor="C Co", workers=4),
            ])
        out = Path(TemplateFiller(cfg).fill(rep, str(temp_dir / "r.ods")))
        grid = _texts(out)
        a, b, c = (self._row_for(grid, n) for n in ("A Co", "B Co", "C Co"))
        assert (a[5], a[7], a[8], a[6]) == ("10", "7", "3", "")
        assert (b[5], b[7], b[8]) == ("7", "2", "5")  # helpers derived
        assert (c[7], c[8]) == ("", "")              # unknown, never 0
        (tot,) = [r for r in grid if r[2:6] and "Total:" in " ".join(r)]
        assert tot[5] == "21" and tot[7] == "9" and tot[8] == "8"

    def _row_for(self, grid, contractor):
        (row,) = [r for r in grid if len(r) > 8 and r[2] == contractor]
        return row

    def test_missing_workers_header_fails_loudly(self, config, temp_dir: Path):
        from app.libre.filler import LibreFillError

        tpl = temp_dir / "broken.ods"
        _relabel_header(SMALL, str(tpl), {5: "People"})
        cfg = config.model_copy(update={"template": config.template.model_copy(
            update={"small_template": str(tpl)})})
        with pytest.raises(LibreFillError):
            TemplateFiller(cfg).fill(_report(1), str(temp_dir / "x.ods"))

    def test_ambiguous_headers_fail_loudly(self, config, temp_dir: Path):
        from app.libre.filler import LibreFillError

        tpl = temp_dir / "dupe.ods"
        _relabel_header(SMALL, str(tpl), {2: "Contractor + Helpers"})
        cfg = config.model_copy(update={"template": config.template.model_copy(
            update={"small_template": str(tpl)})})
        with pytest.raises(LibreFillError):
            TemplateFiller(cfg).fill(_report(1), str(temp_dir / "y.ods"))


class TestContractorReport:
    def _entries(self):
        return [
            {"date": "2026-09-07", "workers": 5, "zone": "A",
             "details": "d", "added_by": "u", "role": "r"},
            {"date": "2026-09-08", "workers": 3, "zone": "B",
             "details": "e", "added_by": "u", "role": "r"},
        ]

    def test_info_entries_summary(self, temp_dir: Path):
        out = ContractorReportFiller("templates/contractor_report_template.ots").fill(
            "Alpha Co", "2026-09-01", "2026-09-08",
            self._entries(), temp_dir / "c.ods",
        )
        assert Path(out).exists()
        flat = " | ".join(" ".join(r) for r in _texts(Path(out)))
        assert "Alpha Co" in flat and "2026-09-01 to 2026-09-08" in flat
        assert "Summary: 2 entries, 8 total workers" in flat


class TestTablesMaster:
    def test_skeleton_loads_empty(self, config):
        """Skeleton tables.ods ships headers only, so counts are zero."""
        reader = TablesReaderService(config)
        assert reader.get_contractor_count() == 0
        assert reader.get_zone_count() == 0

    def test_search_and_counts(self, temp_dir: Path, config):
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableCell, TableRow
        from odf.text import P

        def sheet(doc, name, headers, rows):
            t = Table(name=name)
            for values in [headers] + rows:
                r = TableRow()
                for v in values:
                    c = TableCell()
                    p = P()
                    p.addText(v)
                    c.addElement(p)
                    r.addElement(c)
                t.addElement(r)
            doc.spreadsheet.addElement(t)

        doc = OpenDocumentSpreadsheet()
        sheet(doc, "tblContractor", ["Contractor", "Type"],
              [["Alpha Co", "Civil"], ["Beta Co", "Electrical"]])
        sheet(doc, "tblZones", ["Zone"], [["A"], ["B"]])
        p = temp_dir / "t.ods"
        doc.save(str(p))

        from app.models.config import AppConfig

        cfg = AppConfig(
            template={"file": "t.ots", "tables_file": str(p)},
            date={"cell": "B4", "day_cell": "D4"},
            table={"start_row": 12, "columns": {
                "serial": "A", "contractor": "B", "type": "C",
                "zone": "D", "workers": "E", "details": "F"}},
            output={"pdf_folder": "x", "docs_folder": "y"},
            database={"path": ":memory:"},
            tables={"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
        )
        reader = TablesReaderService(cfg)
        assert reader.get_contractor_count() == 2
        assert reader.get_zone_count() == 2
        assert reader.search_contractors("alpha")[0].name == "Alpha Co"


class TestHistoryFile:
    def test_register_and_read(self, temp_dir: Path):
        from app.database.history_service import HistoryService
        from app.models.database import ReportStatus

        svc = HistoryService(str(temp_dir / "history.ods"))
        assert svc.get_history() == []
        rep = Report(date="2026-09-08", day="Monday",
                     status=ReportStatus.GENERATED, telegram_user="u",
                     created_at="2026-09-08T10:00:00")
        svc.register_report(rep, pdf_path="p.pdf", doc_path="d.ods")
        rows = svc.get_history()
        assert len(rows) == 1
        assert rows[0]["Date"] == "2026-09-08"
        assert rows[0]["Status"] == "Generated"
        assert rows[0]["Document"] == "d.ods"


class TestSofficePdf:
    def test_render_real_template(self, temp_dir: Path, config):
        from app.libre.pdf import PDFGenerator, find_soffice

        try:
            find_soffice()
        except Exception:
            pytest.skip("soffice not available")
        out = Path(TemplateFiller(config).fill(_report(2), str(temp_dir / "r.ods")))
        pdf = Path(PDFGenerator(config).convert_to_pdf(
            str(out), str(temp_dir / "r.pdf")))
        assert pdf.exists() and pdf.stat().st_size > 1000
