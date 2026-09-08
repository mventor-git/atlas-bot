"""Comparison models for Daily Comparison. (mventor-ticket-019)"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ComparisonResult:
    """Result of comparing two daily labor reports.

    Contains summary statistics and detailed diffs between two reports
    (date_a and date_b), including added/removed/changed contractors.
    """

    date_a: str
    """First report date (YYYY-MM-DD)."""

    date_b: str
    """Second report date (YYYY-MM-DD)."""

    contractor_count_a: int = 0
    """Number of contractors in report A."""

    contractor_count_b: int = 0
    """Number of contractors in report B."""

    worker_count_a: int = 0
    """Total workers in report A."""

    worker_count_b: int = 0
    """Total workers in report B."""

    added_contractors: list[tuple[str, int]] = field(default_factory=list)
    """Contractors present in B but not in A, with their worker counts.
    Each tuple is (contractor_name, workers_b)."""

    removed_contractors: list[tuple[str, int]] = field(default_factory=list)
    """Contractors present in A but not in B, with their worker counts.
    Each tuple is (contractor_name, workers_a)."""

    changed_workers: list[tuple[str, int, int]] = field(default_factory=list)
    """Contractors present in both reports but with different worker counts.
    Each tuple is (contractor_name, workers_a, workers_b)."""

    status_a: Optional[str] = None
    """Status of report A (draft/final/locked/no_report)."""

    status_b: Optional[str] = None
    """Status of report B (draft/final/locked/no_report)."""

    @property
    def has_differences(self) -> bool:
        """Return True when any difference exists between the two reports."""
        return bool(
            self.contractor_count_a != self.contractor_count_b
            or self.worker_count_a != self.worker_count_b
            or self.added_contractors
            or self.removed_contractors
            or self.changed_workers
        )

    @property
    def contractor_count_diff(self) -> int:
        """Return the difference in contractor count (B - A)."""
        return self.contractor_count_b - self.contractor_count_a

    @property
    def worker_count_diff(self) -> int:
        """Return the difference in worker count (B - A)."""
        return self.worker_count_b - self.worker_count_a

    def format_summary(self) -> str:
        """Format a human-readable summary of the comparison.

        Returns:
            Markdown-formatted comparison text suitable for Telegram.
        """
        lines = [
            f"\U0001f4ca *Daily Report Comparison*",
            f"",
            f"*Report A:* {self.date_a} (_status_a or 'N/A'_)",
            f"*Report B:* {self.date_b} (_status_b or 'N/A'_)",
            f"",
            f"*Contractors:* {self.contractor_count_a} \u2192 {self.contractor_count_b}",
            f"*Workers:* {self.worker_count_a} \u2192 {self.worker_count_b}",
        ]

        diff_c = self.contractor_count_diff
        diff_w = self.worker_count_diff
        if diff_c > 0:
            lines.append(f"  \u2191 +{diff_c} contractors")
        elif diff_c < 0:
            lines.append(f"  \u2193 {diff_c} contractors")
        if diff_w > 0:
            lines.append(f"  \u2191 +{diff_w} workers")
        elif diff_w < 0:
            lines.append(f"  \u2193 {diff_w} workers")

        if self.added_contractors:
            lines.append(f"")
            lines.append(f"\U0001f7e2 *Added Contractors:*")
            for name, workers in self.added_contractors:
                lines.append(f"  \u2022 {name} \u2014 {workers} workers")

        if self.removed_contractors:
            lines.append(f"")
            lines.append(f"\U0001f534 *Removed Contractors:*")
            for name, workers in self.removed_contractors:
                lines.append(f"  \u2022 {name} \u2014 {workers} workers")

        if self.changed_workers:
            lines.append(f"")
            lines.append(f"\U0001f7e1 *Changed Worker Counts:*")
            for name, workers_a, workers_b in self.changed_workers:
                diff = workers_b - workers_a
                arrow = "\u2191" if diff > 0 else "\u2193"
                lines.append(f"  \u2022 {name}: {workers_a} \u2192 {workers_b} ({arrow}{abs(diff)})")

        if not self.has_differences:
            lines.append(f"")
            lines.append(f"\u2705 *No differences* \u2014 the two reports are identical.")

        return "\n".join(lines)
