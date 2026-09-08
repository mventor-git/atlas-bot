"""
Pytest configuration and fixtures for Labor-Report tests.
"""

import tempfile
from pathlib import Path
from typing import Generator

import pytest
import yaml

from app.config.loader import ConfigLoader
from app.utils.logger import setup_logger


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
            "file": "templates/Daily Labor Report.xlsx",
            "tables_file": "database/tables.xlsx",
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
            "excel_folder": "exports/excel",
        },
        "database": {
            "path": "database/labor_reports.db",
        },
        "history": {
            "file": "database/history.xlsx",
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
