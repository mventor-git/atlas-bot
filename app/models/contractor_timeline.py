"""Contractor Timeline models for mventor-ticket-016.

Defines the dataclasses for contractor timeline entries with
paginated results for display in the Telegram bot.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TimelineEntry:
    """A single entry in a contractor's historical timeline.

    Represents one day where the contractor worked, including
    worker count, zone, details, and file references.
    """

    date: str
    """Report date in YYYY-MM-DD format."""

    day: str
    """Day name in Arabic (e.g., 'الاثنين')."""

    workers: Optional[int] = None
    """Number of workers for this contractor on this date."""

    zone: Optional[str] = None
    """Work zone where the contractor operated."""

    details: Optional[str] = None
    """Work details or notes."""

    contractor_code: Optional[str] = None
    """Contractor code from tables.xlsx."""

    report_id: Optional[int] = None
    """Database ID of the parent report."""

    report_status: Optional[str] = None
    """Report status (draft, final, locked)."""

    pdf_path: Optional[str] = None
    """Path to the generated PDF file."""

    excel_path: Optional[str] = None
    """Path to the generated Excel file."""


@dataclass
class TimelineResult:
    """Paginated timeline result for a contractor.

    Contains the list of timeline entries for the current page,
    along with pagination metadata.
    """

    contractor: str
    """Contractor name the timeline is for."""

    entries: list[TimelineEntry] = field(default_factory=list)
    """Timeline entries for the current page."""

    total_entries: int = 0
    """Total number of entries across all pages."""

    page: int = 1
    """Current page number (1-indexed)."""

    page_size: int = 10
    """Number of entries per page."""

    total_pages: int = 0
    """Total number of pages available."""

    @property
    def has_previous(self) -> bool:
        """Check if there is a previous page available."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """Check if there is a next page available."""
        return self.page < self.total_pages

    @property
    def start_index(self) -> int:
        """Get the 1-indexed start position for display."""
        if self.total_entries == 0:
            return 0
        return (self.page - 1) * self.page_size + 1

    @property
    def end_index(self) -> int:
        """Get the 1-indexed end position for display."""
        if self.total_entries == 0:
            return 0
        return min(self.page * self.page_size, self.total_entries)
