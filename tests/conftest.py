"""
Pytest configuration and fixtures for Labor-Report tests.
"""

import tempfile
from pathlib import Path
from typing import Generator

import pytest
import yaml

from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableCell, TableRow
from odf.text import P

from app.config.loader import ConfigLoader
from app.utils.logger import setup_logger


def make_tables_ods(file_path: Path, contractors: list, zones: list) -> None:
    """Write a minimal master .ods (tblContractor + tblZones headers + rows)."""
    doc = OpenDocumentSpreadsheet()
    for name, headers, rows in (
        ("tblContractor", ["Contractor", "Type"], contractors),
        ("tblZones", ["Zone"], [(z,) for z in zones]),
    ):
        table = Table(name=name)
        for values in [headers] + [list(r) for r in rows]:
            row = TableRow()
            for value in values:
                cell = TableCell()
                p = P()
                p.addText(value)
                cell.addElement(p)
                row.addElement(cell)
            table.addElement(row)
        doc.spreadsheet.addElement(table)
    doc.save(str(file_path))


@pytest.fixture(autouse=True)
def reset_config_singleton() -> Generator:
    """Reset the ConfigLoader singleton before each test."""
    ConfigLoader.reset()
    yield
    ConfigLoader.reset()


@pytest.fixture
def sample_config_dict() -> dict:
    """Provide a minimal valid configuration dictionary."""
    return {
        "template": {
            "file": "templates/report.ots",
            "tables_file": "database/tables.ods",
        },
        "date": {
            "cell": "B4",
            "day_cell": "D4",
        },
        "table": {
            "start_row": 12,
            "columns": {
                "serial": "A",
                "contractor": "B",
                "type": "C",
                "zone": "D",
                "workers": "E",
                "details": "F",
            },
        },
        "output": {
            "pdf_folder": "exports/pdf",
            "docs_folder": "exports/docs",
        },
        "database": {
            "path": "database/labor_reports.db",
        },
        "history": {
            "file": "database/history.ods",
        },
        "logging": {
            "file": "logs/app.log",
            "level": "INFO",
            "max_bytes": 10485760,
            "backup_count": 5,
        },
        "tables": {
            "contractor_sheet": "tblContractor",
            "zones_sheet": "tblZones",
        },
    }


@pytest.fixture
def temp_config_file(sample_config_dict: dict) -> Generator[Path, None, None]:
    """Create a temporary config file for testing."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(sample_config_dict, f)
        temp_path = Path(f.name)

    yield temp_path

    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def logger():
    """Provide a test logger."""
    return setup_logger(name="test_labor_report")
