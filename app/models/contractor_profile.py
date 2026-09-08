"""Contractor Profile models for mventor-ticket-017.

Defines the dataclass for a contractor's profile containing
aggregated statistics and history across all reports.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ContractorProfile:
    """Aggregated profile for a single contractor.

    Computed from all report items across the entire database.
    Includes statistics, favorite zones, and monthly activity.
    """

    name: str
    """Contractor name."""

    type: Optional[str] = None
    """Contractor type from tables.xlsx (e.g. 'Civil', 'Electrical')."""

    code: Optional[str] = None
    """Contractor code from tables.xlsx."""

    first_appearance: Optional[str] = None
    """Earliest date this contractor appears in any report (YYYY-MM-DD)."""

    last_appearance: Optional[str] = None
    """Most recent date this contractor appears in any report (YYYY-MM-DD)."""

    total_reports: int = 0
    """Number of distinct reports this contractor appears in."""

    total_workers: int = 0
    """Sum of all workers across all appearances."""

    avg_workers: float = 0.0
    """Average workers per appearance (total_workers / total_reports)."""

    favorite_zones: list[tuple[str, int]] = field(default_factory=list)
    """Top work zones by frequency, sorted descending. Each entry is (zone_name, count)."""

    monthly_activity: list[tuple[str, int]] = field(default_factory=list)
    """Worker counts grouped by month. Each entry is (YYYY-MM, total_workers)."""
