"""
Database models for Labor-Report.

Defines data classes for Report and ReportItem used throughout
the application for type-safe data exchange between layers.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass
class Contractor:
    """A contractor from the tblContractor sheet."""

    name: str
    """Contractor name."""

    type: Optional[str] = None
    """Contractor type (e.g., Civil, Electrical)."""


@dataclass
class Zone:
    """A work zone from the tblZones sheet."""

    name: str
    """Zone name."""


@dataclass
class RecentContractor:
    """A recently used contractor entry."""

    contractor_name: str
    """Contractor name."""

    telegram_user: str
    """Telegram user who used this contractor."""

    last_used: str
    """ISO datetime of last use."""

    use_count: int = 1
    """Number of times used."""

    id: Optional[int] = None
    """Database ID."""


class ReportStatus(str, Enum):
    """Status of a daily labor report.

    v2.0: Extended from generated/no_report to full lifecycle:
    - DRAFT: Report being edited (freely modifiable)
    - FINAL: Official report (read-only, PDF generated)
    - LOCKED: Permanently read-only (admin may unlock)
    - NO_REPORT: No labor occurred on this date
    """

    DRAFT = "draft"
    """Report is being edited (freely modifiable). (NEW v2.0)"""

    FINAL = "final"
    """Official report â€” read-only, PDF generated. (NEW v2.0, replaces GENERATED)"""

    LOCKED = "locked"
    """Permanently read-only â€” admin may unlock. (NEW v2.0)"""

    NO_REPORT = "no_report"
    """No labor report for this date (recorded as absence)."""

    # Legacy aliases for backward compatibility
    GENERATED = "final"
    """Legacy alias for FINAL (v1.0 compatibility)."""


@dataclass
class ReportItem:
    """A single row in the daily labor report table.

    Represents one contractor's workers for a given day.
    v2.0: Added contractor_code for universal search.
    """

    contractor: str
    """Name of the contractor (from tblContractor)."""

    type: Optional[str] = None
    """Type of contractor (e.g., Civil, Electrical)."""

    zone: Optional[str] = None
    """Work zone (optional, from tblZones)."""

    workers: Optional[int] = None
    """Number of workers for this contractor."""

    details: Optional[str] = None
    """Detailed worker breakdown (e.g., '10 Mason, 3 Helper')."""

    contractor_code: Optional[str] = None
    """Contractor code from tables.xlsx (NEW v2.0)."""

    id: Optional[int] = None
    """Database ID (set after persistence)."""

    report_id: Optional[int] = None
    """Parent report ID (set after persistence)."""


@dataclass
class Report:
    """A daily labor report for a specific date.

    Contains header information and a list of report items.
    v2.0: Extended with lifecycle fields (draft/final/locked).
    """

    date: str
    """Report date in YYYY-MM-DD format."""

    day: str
    """Day name in Arabic (e.g., 'Ø§Ù„Ø§Ø«Ù†ÙŠÙ†')."""

    status: ReportStatus = ReportStatus.DRAFT
    """Report lifecycle status: draft, final, locked, or no_report."""

    telegram_user: Optional[str] = None
    """Telegram user identifier who created the report."""

    pdf_path: Optional[str] = None
    """Path to the generated PDF file."""

    excel_path: Optional[str] = None
    """Path to the generated Excel file."""

    preview_pdf_path: Optional[str] = None
    """Path to the preview PDF (v2.0 â€” mventor-ticket-011)."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when the report was created."""

    updated_at: Optional[str] = None
    """ISO datetime when the report was last modified (NEW v2.0)."""

    finalized_at: Optional[str] = None
    """ISO datetime when the report was finalized (NEW v2.0)."""

    locked_at: Optional[str] = None
    """ISO datetime when the report was locked (NEW v2.0)."""

    locked_by: Optional[str] = None
    """Telegram user who locked the report (NEW v2.0)."""

    source_date: Optional[str] = None
    """Original date if this report was copied from another (NEW v2.0)."""

    site_id: Optional[str] = None
    """Tenant site identifier (multi-site isolation; env SITE_ID when unset)."""

    id: Optional[int] = None
    """Database ID (set after persistence)."""

    items: list[ReportItem] = field(default_factory=list)
    """List of report items (contractor rows)."""

    def add_item(self, item: ReportItem) -> None:
        """Add a report item to this report.

        Args:
            item: The ReportItem to add.
        """
        self.items.append(item)

    @property
    def is_draft(self) -> bool:
        """Check if this report is in draft state (NEW v2.0)."""
        return self.status == ReportStatus.DRAFT

    @property
    def is_final(self) -> bool:
        """Check if this report has been finalized (NEW v2.0)."""
        return self.status == ReportStatus.FINAL

    @property
    def is_locked(self) -> bool:
        """Check if this report is locked (NEW v2.0)."""
        return self.status == ReportStatus.LOCKED

    @property
    def is_generated(self) -> bool:
        """Check if this report was actually generated (legacy compatibility)."""
        return self.status in (ReportStatus.FINAL, ReportStatus.LOCKED)

    @property
    def is_no_report(self) -> bool:
        """Check if this date has no report."""
        return self.status == ReportStatus.NO_REPORT

    @property
    def is_editable(self) -> bool:
        """Check if this report can be edited (NEW v2.0)."""
        return self.status == ReportStatus.DRAFT


class SessionState(str, Enum):
    """State of a user's conversation session."""

    IDLE = "idle"
    """User has no active conversation."""

    AWAITING_DATE = "awaiting_date"
    """Waiting for user to provide or confirm the report date."""

    AWAITING_LABOR_YES_NO = "awaiting_labor_yes_no"
    """Waiting for user to answer if labor occurred today."""

    SEARCHING_CONTRACTOR = "searching_contractor"
    """Waiting for user to type a contractor search query."""

    SELECTING_CONTRACTOR = "selecting_contractor"
    """Waiting for user to select a contractor from search results."""

    AWAITING_WORKERS = "awaiting_workers"
    """Waiting for user to enter the number of workers."""

    AWAITING_DETAILS = "awaiting_details"
    """Waiting for user to enter work details (optional)."""

    AWAITING_ZONE = "awaiting_zone"
    """Waiting for user to select or enter a work zone."""

    AWAITING_MORE_CONTRACTORS = "awaiting_more_contractors"
    """Waiting for user to add another contractor or finish."""

    AWAITING_CONFIRMATION = "awaiting_confirmation"
    """Waiting for user to confirm or cancel the report."""

    # --- v2.0 Extended States ---
    AWAITING_COPY_YESTERDAY = "awaiting_copy_yesterday"
    """Waiting for user to confirm copying yesterday's report. (NEW v2.0)"""

    AWAITING_PREVIEW = "awaiting_preview"
    """Preview PDF shown, waiting for user action. (NEW v2.0)"""

    AWAITING_SEARCH_QUERY = "awaiting_search_query"
    """Waiting for user to type a universal search query. (NEW v2.0)"""

    AWAITING_SEARCH_RESULTS = "awaiting_search_results"
    """Browsing universal search results. (NEW v2.0)"""

    AWAITING_STATS_PERIOD = "awaiting_stats_period"
    """Waiting for user to select statistics period. (NEW v2.0)"""

    AWAITING_COMPARISON = "awaiting_comparison"
    """Waiting for user to select date for comparison. (NEW v2.0)"""

    AWAITING_CALENDAR = "awaiting_calendar"
    """Calendar navigation mode. (NEW v2.0)"""

    AWAITING_EXPLORER = "awaiting_explorer"
    """Report explorer navigation mode. (NEW v2.0)"""

    AWAITING_ADMIN_ACTION = "awaiting_admin_action"
    """Waiting for admin to confirm action. (NEW v2.0)"""


@dataclass
class UserSession:
    """A user's active conversation session.

    Stores the current state of a multi-step Telegram conversation,
    along with in-session data accumulated so far.
    """

    telegram_user: str
    """Telegram user identifier."""

    state: SessionState = SessionState.IDLE
    """Current conversation state."""

    data: dict = field(default_factory=dict)
    """In-session data accumulated during the conversation.

    Typical keys:
        - date: str â€” selected report date (YYYY-MM-DD)
        - day: str â€” Arabic day name
        - contractors: list[dict] â€” selected contractors with workers
        - current_search: list[str] â€” last search results
    """

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when the session was created."""

    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when the session was last updated."""

    id: Optional[str] = None
    """Session identifier (same as telegram_user for now)."""


# â”€â”€â”€ v2.0 New Data Models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@dataclass
class ReportVersion:
    """A snapshot of a report at a point in time. (NEW v2.0)

    Created each time a report is finalized or explicitly versioned.
    Enables rollback to any previous version.
    """

    report_id: int
    """Parent report ID."""

    version_number: int
    """Version number (1, 2, 3, ...)."""

    snapshot: str
    """JSON snapshot of the complete report state (report + items)."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when this version was created."""

    created_by: Optional[str] = None
    """Telegram user who triggered this version."""

    change_summary: Optional[str] = None
    """Human-readable summary of what changed."""

    id: Optional[int] = None
    """Database ID (set after persistence)."""


@dataclass
class EventLogEntry:
    """An entry in the audit event log. (NEW v2.0)

    Records every significant action for accountability and debugging.
    """

    timestamp: str
    """ISO datetime when the event occurred."""

    telegram_user: str
    """Who performed the action."""

    action: str
    """Action identifier (e.g., 'report.created', 'draft.saved')."""

    object_type: Optional[str] = None
    """Type of affected object ('report', 'report_item', etc.)."""

    object_id: Optional[int] = None
    """ID of the affected object."""

    object_date: Optional[str] = None
    """Date of the affected report (for quick reference)."""

    old_value: Optional[str] = None
    """Previous state (JSON or string)."""

    new_value: Optional[str] = None
    """New state (JSON or string)."""

    site_id: Optional[str] = None
    """Tenant site identifier (multi-site isolation; env SITE_ID when unset)."""

    id: Optional[int] = None
    """Database ID (set after persistence)."""


@dataclass
class FavoriteContractor:
    """A per-user favorite contractor. (NEW v2.0)

    Favorite contractors appear first in search suggestions.
    """

    telegram_user: str
    """Telegram user who favorited this contractor."""

    contractor_name: str
    """Contractor name."""

    contractor_code: Optional[str] = None
    """Contractor code (from tables.xlsx)."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when favorited."""

    id: Optional[int] = None
    """Database ID (set after persistence)."""


@dataclass
class User:
    """A Telegram user with a role in the system. (NEW v3.0)"""

    chat_id: str
    """Telegram chat ID."""

    role: str = "pending"
    """Role: 'superadmin', 'project_manager', 'executive_engineer', 'admin', 'normal_user', 'viewer', 'pending', or 'rejected'."""

    username: Optional[str] = None
    """Telegram username (optional)."""

    first_name: Optional[str] = None
    """Telegram first name (optional)."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when the user was added."""

    approved_by: Optional[str] = None
    """Chat ID of admin who approved this user."""

    approved_at: Optional[str] = None
    """ISO datetime of approval."""

    site_id: Optional[str] = None
    """Assigned site (multi-site notify/print targeting; env SITE_ID when unset)."""

    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime of last update."""

    id: Optional[int] = None
    """Database ID (set after persistence)."""


@dataclass
class StatsCache:
    """Pre-computed statistics for fast dashboard display. (NEW v2.0)

    Avoids expensive aggregation queries on every dashboard load.
    """

    period: str
    """Period type: 'daily', 'monthly', or 'yearly'."""

    period_key: str
    """Period identifier: '2026-07-11', '2026-07', or '2026'."""

    data: str
    """JSON blob with aggregated statistics."""

    computed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when this cache entry was computed."""

    id: Optional[int] = None
    """Database ID (set after persistence)."""
