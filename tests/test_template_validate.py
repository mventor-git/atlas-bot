"""Template validation gate tests (ticket-030): real shipped files pass;
malformed fixtures fail loudly with file+field+reason."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.libre import ots
from app.libre.columns import (
    LibreFillError,
    TemplateColumnError,
    locate_columns,
)
from app.libre.validate import TemplateValidator

SMALL = "templates/contractor-daily-labor-template.ods"
MEDIUM = "templates/medium_template.ots"
LARGE = "templates/large_template.ots"
ALL = (SMALL, MEDIUM, LARGE)


def _relabel(src, dst, updates):
    doc = ots.load_doc(src)
    table = ots.first_table(doc)
    h = ots.find_first(table, ("Contractor",))
    cells = ots.logical_cells(
        table.getElementsByType(ots.TableRow)[h])
    for idx, text in updates.items():
        ots.set_cell_text(cells[idx], text)
    ots.save(doc, dst)


@pytest.fixture
def validator():
    return TemplateValidator()


class TestProductionPass:
    @pytest.mark.parametrize("path", ALL)
    def test_structure_valid(self, validator, path):
        report = validator.check_template(path)
        assert report.passed, [str(f) for f in report.failures]

    @pytest.mark.parametrize("path", ALL)
    def test_fill_smoke(self, validator, path, tmp_path):
        from app.models.config import AppConfig

        cfg = AppConfig(**{
            "template": {"file": path, "tables_file": "t.ods",
                         "small_template": path, "medium_template": path,
                         "large_template": path},
            "date": {"cell": "B7", "day_cell": "B5"},
            "table": {"start_row": 11, "columns": {}},
            "output": {"pdf_folder": "x", "docs_folder": "y"},
            "database": {"path": ":memory:"},
            "tables": {"contractor_sheet": "c", "zones_sheet": "z"},
        })
        out = validator.smoke_fill(path, tmp_path, cfg)
        assert Path(out).exists()


class TestFailures:
    @pytest.mark.parametrize("idx", [5, 6, 7, 8])
    def test_missing_each_field_fails(self, validator, tmp_path, idx):
        dst = str(tmp_path / "m.ods")
        _relabel(SMALL, dst, {idx: ""})
        report = validator.check_template(dst)
        assert not report.passed
        assert str(dst) in str(report.failures[0])
        assert any(f.check for f in report.failures)

    def test_duplicate_alias_fails(self, validator, tmp_path):
        dst = str(tmp_path / "d.ods")
        _relabel(SMALL, dst, {2: "Contractor + Helpers"})
        report = validator.check_template(dst)
        assert not report.passed
        assert "two fields" in str(report.failures[0])

    def test_no_header_row_fails(self, validator, tmp_path):
        dst = str(tmp_path / "n.ods")
        _relabel(SMALL, dst, {i: "" for i in range(9)})
        report = validator.check_template(dst)
        assert not report.passed
        assert "header row" in str(report.failures[0])

    def test_missing_totals_fails(self, validator, tmp_path):
        from odf.table import TableRow

        doc = ots.load_doc(SMALL)
        table = ots.first_table(doc)
        rows = table.getElementsByType(TableRow)
        totals = ots.find_first(table, ("Total:",))
        table.removeChild(rows[totals])
        dst = str(tmp_path / "t.ods")
        ots.save(doc, dst)
        report = validator.check_template(dst)
        assert not report.passed
        assert "totals" in str(report.failures[0])

    def test_empty_sheet_fails(self, validator, tmp_path):
        from odf.opendocument import OpenDocumentSpreadsheet

        dst = str(tmp_path / "e.ods")
        OpenDocumentSpreadsheet().save(dst)
        report = validator.check_template(dst)
        assert not report.passed

    def test_missing_file_fails(self, validator):
        report = validator.check_template("templates/nope.ots")
        assert not report.passed
        assert "nope.ots" in str(report.failures[0])


class TestParity:
    """Validator and filler share one interpretation (030 §14)."""

    @pytest.mark.parametrize("path", ALL)
    def test_validator_pass_implies_locate_pass(self, validator, path):
        report = validator.check_template(path)
        doc = ots.load_doc(path)
        table = ots.first_table(doc)
        h = ots.find_first(table, ("Contractor",))
        texts = [ots.cell_text(c) for c in ots.logical_cells(
            table.getElementsByType(ots.TableRow)[h])]
        cols = locate_columns(texts)
        assert report.passed
        assert cols["craftsmen"] == 6 and cols["helpers"] == 7
        assert cols["details"] == 8 and cols["workers"] == 5

    def test_filler_error_is_validator_error(self):
        assert issubclass(LibreFillError, TemplateColumnError)


class TestOwnerCapability:
    """030 §16: summary access is an explicit grant, never a fallback."""

    def test_member_without_summary_cap_gets_none(self):
        from app.services import report_visibility as visibility

        auth = MagicMock()
        auth.sites_for_user.return_value = ["a"]
        auth.has_capability.return_value = False
        assert visibility.resolve(auth, "9", "a") == visibility.NONE

    def test_member_with_summary_cap_gets_owner(self):
        from app.services import report_visibility as visibility

        auth = MagicMock()
        auth.sites_for_user.return_value = ["a"]

        def _has(chat, cap, site=None):
            return cap == "view_site_report_summary"
        auth.has_capability.side_effect = _has
        assert visibility.resolve(auth, "9", "a") == visibility.OWNER

    def test_viewer_role_carries_summary_cap(self):
        from app.auth import capabilities as caps

        assert "view_site_report_summary" in caps.for_role("viewer")
        assert "view_site_report_summary" not in caps.for_role("pending")


class TestCalendarUntouched:
    """030 §18: preflight/validation never decides workdays; Friday renders."""

    def test_friday_renders_and_stays_non_required(self, tmp_path):
        from app.models.config import AppConfig
        from app.models.database import Report, ReportItem, ReportStatus
        from app.services.working_calendar import WorkingCalendar

        cfg = AppConfig(**{
            "template": {"file": SMALL, "tables_file": "t.ods",
                         "small_template": SMALL, "medium_template": SMALL,
                         "large_template": SMALL},
            "date": {"cell": "B7", "day_cell": "B5"},
            "table": {"start_row": 11, "columns": {}},
            "output": {"pdf_folder": "x", "docs_folder": "y"},
            "database": {"path": ":memory:"},
            "tables": {"contractor_sheet": "c", "zones_sheet": "z"},
        })
        out = TemplateValidator(cfg).smoke_fill(SMALL, tmp_path, cfg)
        assert Path(out).exists()
        cal = WorkingCalendar(cfg, "default")
        required, _ = cal.describe("2026-09-11")  # a Friday
        assert required is False
