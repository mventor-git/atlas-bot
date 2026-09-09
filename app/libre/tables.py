"""Master tables reader on LibreOffice .ods (odfpy).

Same public API as the legacy reader: load/is_loaded/getters/search
with Arabic normalization. Reads ``database/tables.ods``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.text import P

from app.models.config import AppConfig
from app.models.database import Contractor, Zone
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TablesReaderError(DatabaseError):
    """Raised when reading the tables file fails."""


def _cell_text(cell) -> str:
    return "/".join(
        "".join(str(n) for n in p.childNodes if n.nodeType == 3)
        for p in cell.getElementsByType(P)
    ).strip()


def _sheet_rows(doc, name: str) -> list[list[str]]:
    for table in doc.getElementsByType(Table):
        if table.getAttribute("name") != name:
            continue
        out = []
        for row in table.getElementsByType(TableRow):
            vals: list[str] = []
            for cell in row.getElementsByType(TableCell):
                reps = int(cell.getAttribute("numbercolumnsrepeated") or 1)
                vals.extend([_cell_text(cell)] * reps)
            out.append(vals)
        return out
    raise TablesReaderError(f"Sheet '{name}' not found.")


class TablesReaderService:
    """Reads contractor/zone master data from tables.ods (cached)."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._tables_path = config.tables_file_path
        self._contractor_sheet = config.tables.contractor_sheet
        self._zones_sheet = config.tables.zones_sheet
        self._contractors: list[Contractor] = []
        self._zones: list[Zone] = []
        self._loaded = False

    def load(self) -> None:
        """Load master data (reloads every call)."""
        if not self._tables_path.exists():
            raise TablesReaderError(
                f"Tables file not found: {self._tables_path}"
            )
        try:
            doc = load(str(self._tables_path))
            self._contractors = [
                Contractor(name=r[0].strip(), type=r[1].strip() if len(r) > 1 and r[1].strip() else None)
                for r in _sheet_rows(doc, self._contractor_sheet)[1:]
                if r and r[0].strip()
            ]
            self._zones = [
                Zone(name=r[0].strip())
                for r in _sheet_rows(doc, self._zones_sheet)[1:]
                if r and r[0].strip()
            ]
            self._loaded = True
            logger.info(
                "Tables loaded: %d contractors, %d zones from %s",
                len(self._contractors), len(self._zones), self._tables_path,
            )
        except TablesReaderError:
            raise
        except Exception as e:
            raise TablesReaderError(
                f"Failed to read tables file: {e}", original_exception=e
            ) from e

    def is_loaded(self) -> bool:
        return self._loaded

    def get_all_contractors(self) -> list[Contractor]:
        self._ensure_loaded()
        return list(self._contractors)

    def get_all_zones(self) -> list[Zone]:
        self._ensure_loaded()
        return list(self._zones)

    @staticmethod
    def _normalize_arabic(text: str) -> str:
        text = text.replace("\u0623", "\u0627").replace("\u0625", "\u0627")
        text = text.replace("\u0622", "\u0627").replace("\u0649", "\u064A")
        return text.replace("\u0629", "\u0647")

    def search_contractors(self, query: str, max_results: int = 20) -> list[Contractor]:
        self._ensure_loaded()
        if not query or not query.strip():
            return self.get_all_contractors()[:max_results]
        q = self._normalize_arabic(query.strip().lower())
        results = [c for c in self._contractors if q in self._normalize_arabic(c.name.lower())]
        results.sort(key=lambda c: (
            0 if self._normalize_arabic(c.name.lower()) == q else
            1 if self._normalize_arabic(c.name.lower()).startswith(q) else 2
        ))
        return results[:max_results]

    def get_contractor_by_name(self, name: str) -> Optional[Contractor]:
        self._ensure_loaded()
        target = self._normalize_arabic(name.strip().lower())
        for c in self._contractors:
            if self._normalize_arabic(c.name.lower()) == target:
                return c
        return None

    def get_contractor_count(self) -> int:
        self._ensure_loaded()
        return len(self._contractors)

    def get_zone_count(self) -> int:
        self._ensure_loaded()
        return len(self._zones)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()
