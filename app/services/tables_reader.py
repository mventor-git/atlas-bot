"""
Tables Reader Service for Labor-Report.

Reads contractor and zone data from the tables.xlsx workbook.
Provides searchable access to contractors with partial matching.
"""

from pathlib import Path
from typing import Optional

from app.models.config import AppConfig
from app.models.database import Contractor, Zone
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TablesReaderError(DatabaseError):
    """Raised when reading tables.xlsx fails."""


class TablesReaderService:
    """Reads and provides access to contractor/zone data from tables.xlsx.

    Loads data on initialization and provides search/filter methods.
    Data is cached in memory after loading.

    Usage:
        reader = TablesReaderService(config)
        contractors = reader.search_contractors("civil")
        zones = reader.get_all_zones()
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize the tables reader.

        Args:
            config: Application configuration with tables file path
                    and sheet names.
        """
        self._config = config
        self._tables_path = config.tables_file_path
        self._contractor_sheet = config.tables.contractor_sheet
        self._zones_sheet = config.tables.zones_sheet

        # Cached data
        self._contractors: list[Contractor] = []
        self._zones: list[Zone] = []
        self._loaded = False

    # --- Public API ---

    def load(self) -> None:
        """Load contractor and zone data from tables.xlsx.

        Can be called multiple times; reloads data each time.

        Raises:
            TablesReaderError: If the file is missing or data cannot be read.
        """
        if not self._tables_path.exists():
            raise TablesReaderError(
                f"Tables file not found: {self._tables_path}\n"
                f"Please ensure the file exists at: {self._tables_path}"
            )

        try:
            self._contractors = self._read_contractors()
            self._zones = self._read_zones()
            self._loaded = True
            logger.info(
                "Tables loaded: %d contractors, %d zones from %s",
                len(self._contractors), len(self._zones), self._tables_path,
            )
        except TablesReaderError:
            raise
        except Exception as e:
            raise TablesReaderError(
                f"Failed to read tables file: {e}",
                original_exception=e,
            ) from e

    def is_loaded(self) -> bool:
        """Check if data has been loaded."""
        return self._loaded

    def get_all_contractors(self) -> list[Contractor]:
        """Get all contractors.

        Returns:
            List of all contractors. Loads data if not already loaded.
        """
        self._ensure_loaded()
        return list(self._contractors)

    def get_all_zones(self) -> list[Zone]:
        """Get all work zones.

        Returns:
            List of all zones. Loads data if not already loaded.
        """
        self._ensure_loaded()
        return list(self._zones)

    @staticmethod
    def _normalize_arabic(text: str) -> str:
        """Normalize Arabic text for fuzzy matching.

        Normalizes:
        - Alef variants (أ, إ, آ) -> ا
        - Yeh variants (ي, ى) -> ي
        - Teh Marbuta (ة) -> ه

        Args:
            text: The text to normalize.

        Returns:
            Normalized text.
        """
        text = text.replace("\u0623", "\u0627")  # أ -> ا
        text = text.replace("\u0625", "\u0627")  # إ -> ا
        text = text.replace("\u0622", "\u0627")  # آ -> ا
        text = text.replace("\u0649", "\u064A")  # ى -> ي
        text = text.replace("\u0629", "\u0647")  # ة -> ه
        return text

    def search_contractors(self, query: str, max_results: int = 20) -> list[Contractor]:
        """Search contractors by name (case-insensitive, partial match).

        Args:
            query: Search text (partial contractor name).
            max_results: Maximum number of results to return.

        Returns:
            List of matching contractors, sorted by name.
        """
        self._ensure_loaded()

        if not query or not query.strip():
            return self.get_all_contractors()[:max_results]

        query_norm = self._normalize_arabic(query.strip().lower())
        results = [
            c for c in self._contractors
            if query_norm in self._normalize_arabic(c.name.lower())
        ]
        # Sort by relevance: exact matches first, then starts with, then contains
        results.sort(key=lambda c: (
            0 if self._normalize_arabic(c.name.lower()) == query_norm else
            1 if self._normalize_arabic(c.name.lower()).startswith(query_norm) else
            2
        ))
        return results[:max_results]

    def get_contractor_by_name(self, name: str) -> Optional[Contractor]:
        """Find a contractor by exact name (case-insensitive).

        Args:
            name: The exact contractor name.

        Returns:
            The Contractor if found, None otherwise.
        """
        self._ensure_loaded()
        name_norm = self._normalize_arabic(name.strip().lower())
        for c in self._contractors:
            if self._normalize_arabic(c.name.lower()) == name_norm:
                return c
        return None

    def get_contractor_count(self) -> int:
        """Get the total number of contractors.

        Returns:
            Number of contractors. Loads data if not already loaded.
        """
        self._ensure_loaded()
        return len(self._contractors)

    def get_zone_count(self) -> int:
        """Get the total number of zones.

        Returns:
            Number of zones. Loads data if not already loaded.
        """
        self._ensure_loaded()
        return len(self._zones)

    # --- Private helpers ---

    def _ensure_loaded(self) -> None:
        """Load data if not already loaded."""
        if not self._loaded:
            self.load()

    def _read_contractors(self) -> list[Contractor]:
        """Read contractors from the tblContractor sheet.

        Expected columns:
            A: Contractor name
            B: Type (optional)

        Returns:
            List of Contractor dataclass instances.

        Raises:
            TablesReaderError: If reading fails.
        """
        try:
            import openpyxl
        except ImportError as e:
            raise TablesReaderError(
                "openpyxl is required to read tables.xlsx.",
                original_exception=e,
            ) from e

        try:
            wb = openpyxl.load_workbook(str(self._tables_path), read_only=True, data_only=True)
        except Exception as e:
            raise TablesReaderError(
                f"Cannot open tables file: {e}",
                original_exception=e,
            ) from e

        if self._contractor_sheet not in wb.sheetnames:
            wb.close()
            raise TablesReaderError(
                f"Sheet '{self._contractor_sheet}' not found in {self._tables_path}. "
                f"Available sheets: {wb.sheetnames}"
            )

        ws = wb[self._contractor_sheet]
        contractors: list[Contractor] = []
        row_count = 0

        for row in ws.iter_rows(min_row=2, max_col=2, values_only=True):
            name, ctype = row
            if name is not None and str(name).strip():
                contractors.append(
                    Contractor(
                        name=str(name).strip(),
                        type=str(ctype).strip() if ctype is not None else None,
                    )
                )
                row_count += 1

        wb.close()
        logger.debug("Read %d contractors from sheet '%s'", row_count, self._contractor_sheet)
        return contractors

    def _read_zones(self) -> list[Zone]:
        """Read work zones from the tblZones sheet.

        Expected column:
            A: Zone name

        Returns:
            List of Zone dataclass instances.

        Raises:
            TablesReaderError: If reading fails.
        """
        try:
            import openpyxl
        except ImportError as e:
            raise TablesReaderError(
                "openpyxl is required to read tables.xlsx.",
                original_exception=e,
            ) from e

        try:
            wb = openpyxl.load_workbook(str(self._tables_path), read_only=True, data_only=True)
        except Exception as e:
            raise TablesReaderError(
                f"Cannot open tables file: {e}",
                original_exception=e,
            ) from e

        if self._zones_sheet not in wb.sheetnames:
            wb.close()
            raise TablesReaderError(
                f"Sheet '{self._zones_sheet}' not found in {self._tables_path}. "
                f"Available sheets: {wb.sheetnames}"
            )

        ws = wb[self._zones_sheet]
        zones: list[Zone] = []
        row_count = 0

        for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
            name = row[0]
            if name is not None and str(name).strip():
                zones.append(Zone(name=str(name).strip()))
                row_count += 1

        wb.close()
        logger.debug("Read %d zones from sheet '%s'", row_count, self._zones_sheet)
        return zones
