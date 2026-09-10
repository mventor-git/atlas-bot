"""
Audit models for Labor-Report.

Tracks user activity: values added, reverted, and view requests.
New database table: user_activity_log
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class UserActivityLog:
    """Record of a user action in the system.

    Logged actions:
    - 'added': User added a contractor entry to a report
    - 'reverted': User/Admin reverted an entry or full report
    - 'viewed': User requested to view/download a report
    - 'exported': User exported/downloaded a PDF
    """

    telegram_user: str
    """Telegram user ID who performed the action."""

    user_role: str
    """User's role at the time of action (superadmin, project_manager, executive_engineer, admin, normal_user, viewer)."""

    action: str
    """Action type: 'added', 'reverted', 'viewed', 'exported'."""

    report_date: Optional[str] = None
    """Date of the affected report (YYYY-MM-DD)."""

    report_status: Optional[str] = None
    """Report status at the time: 'draft', 'final', 'locked'."""

    contractor_name: Optional[str] = None
    """Contractor name (for 'added' or 'reverted' actions)."""

    workers: Optional[int] = None
    """Number of workers for this entry (for 'added' actions)."""

    zone: Optional[str] = None
    """Work zone (for 'added' actions)."""

    details: Optional[str] = None
    """Work details (for 'added' actions)."""

    reverted_entry_id: Optional[int] = None
    """ID of the original activity log entry that was reverted."""

    site_id: Optional[str] = None
    """Tenant site identifier (multi-site isolation; env SITE_ID when unset)."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    """ISO datetime when the action occurred."""

    id: Optional[int] = None
    """Database ID."""
