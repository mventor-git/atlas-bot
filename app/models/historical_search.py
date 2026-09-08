"""Historical search models for mventor-ticket-018."""

from dataclasses import dataclass
from typing import Optional, List


@dataclass
class HistoricalSearchResult:
    """Result of a historical search query."""

    found: bool
    """Whether an exact match was found."""

    date: Optional[str] = None
    """Report date (YYYY-MM-DD)."""

    day: Optional[str] = None
    """Day name in Arabic."""

    zone: Optional[str] = None
    """Zone where work occurred."""

    workers: Optional[int] = None
    """Total workers for this contractor on this date."""

    details: Optional[str] = None
    """Work details/notes."""

    report_id: Optional[int] = None
    """Database ID of the report."""

    pdf_path: Optional[str] = None
    """Path to the PDF report."""

    excel_path: Optional[str] = None
    """Path to the Excel report."""

    contractor: Optional[str] = None
    """Contractor name for this match."""

    contractor_code: Optional[str] = None
    """Contractor code."""

    nearest_previous: Optional["HistoricalSearchResult"] = None
    """Nearest previous working day, if exact not found."""

    nearest_next: Optional["HistoricalSearchResult"] = None
    """Nearest next working day, if exact not found."""


@dataclass
class HistoricalSearchMatch:
    """Simple match result for a specific report item."""

    report_id: int
    date: str
    day: str
    workers: int
    zone: Optional[str] = None
    details: Optional[str] = None
    contractor_code: Optional[str] = None
    pdf_path: Optional[str] = None
    excel_path: Optional[str] = None