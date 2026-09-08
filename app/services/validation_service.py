"""Validation Service for Labor-Report. (mventor-ticket-013)

Warns users about suspicious data before it is persisted.
Every rule is configurable via ValidationConfig.

ValidationWarning severity:
  - 'warning': User may choose to proceed or fix.
  - 'error':   Action should be blocked (e.g., invalid status transition).

Typical usage:
    service = ValidationService(config)
    warnings = service.validate_report(report)
    if any(w.severity == 'error' for w in warnings):
        # Block the action
    elif warnings:
        # Show warnings, let user decide
"""

from datetime import datetime, timedelta
from typing import Optional

from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus


class ValidationWarning:
    """A warning or error produced during validation.

    Attributes:
        field: The field or item that triggered the warning (e.g. 'workers').
        message: Human-readable description of the issue.
        severity: 'warning' (advisory) or 'error' (should block action).
    """

    def __init__(self, field: str, message: str, severity: str = "warning") -> None:
        self.field = field
        self.message = message
        self.severity = severity

    def __repr__(self) -> str:
        return f"ValidationWarning(field={self.field!r}, message={self.message!r}, severity={self.severity!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ValidationWarning):
            return NotImplemented
        return (self.field == other.field
                and self.message == other.message
                and self.severity == other.severity)


class ValidationService:
    """Validates reports, items, dates, and status transitions.

    All thresholds come from ValidationConfig so they can be tuned
    without code changes.
    """

    # Valid status transitions mirroring ReportWorkflowService
    VALID_TRANSITIONS: dict[ReportStatus, set[ReportStatus]] = {
        ReportStatus.DRAFT: {ReportStatus.FINAL},
        ReportStatus.FINAL: {ReportStatus.LOCKED},
        ReportStatus.LOCKED: {ReportStatus.DRAFT},
    }

    def __init__(self, config: AppConfig) -> None:
        """Initialize the validation service.

        Args:
            config: Application configuration (uses validation settings).
        """
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_report(self, report: Report) -> list[ValidationWarning]:
        """Validate an entire report and return all warnings.

        Checks date validity, per-item rules, and report-level rules
        (e.g. too many contractors, empty required fields).

        Args:
            report: The report to validate.

        Returns:
            List of ValidationWarning (empty list = no issues).
        """
        warnings: list[ValidationWarning] = []

        # Date validation
        warnings.extend(self.validate_date(report.date))

        # Day field
        if not report.day or not report.day.strip():
            warnings.append(ValidationWarning(
                field="day",
                message="Report day is empty.",
                severity="warning",
            ))

        # Per-item validation
        for item in report.items:
            warnings.extend(self.validate_item(item, report.items))

        # Report-level: too many contractors
        if len(report.items) > self._config.validation.max_contractors_per_report:
            warnings.append(ValidationWarning(
                field="contractors",
                message=(
                    f"Report has {len(report.items)} contractors, "
                    f"which exceeds the limit of "
                    f"{self._config.validation.max_contractors_per_report}."
                ),
                severity="warning",
            ))

        return warnings

    def validate_item(
        self,
        item: ReportItem,
        existing_items: Optional[list[ReportItem]] = None,
    ) -> list[ValidationWarning]:
        """Validate a single report item.

        Checks:
        - Contractor name is not empty
        - Worker count within configured bounds
        - Duplicate contractor (against existing_items)
        - Empty details (if warn_empty_details is enabled)

        Args:
            item: The item to validate.
            existing_items: Full list of items in the report (for duplicate check).
                            If None, duplicate check is skipped.

        Returns:
            List of ValidationWarning (empty = no issues).
        """
        warnings: list[ValidationWarning] = []

        rules = self._config.validation

        # --- Contractor name ---
        if not item.contractor or not item.contractor.strip():
            warnings.append(ValidationWarning(
                field="contractor",
                message="Contractor name is empty.",
                severity="warning",
            ))

        # --- Worker count: too high ---
        if item.workers is not None and item.workers > rules.max_workers_per_contractor:
            warnings.append(ValidationWarning(
                field="workers",
                message=(
                    f"Worker count ({item.workers}) exceeds the maximum "
                    f"of {rules.max_workers_per_contractor}."
                ),
                severity="warning",
            ))

        # --- Worker count: too low ---
        if item.workers is not None and item.workers < rules.min_workers_per_contractor:
            warnings.append(ValidationWarning(
                field="workers",
                message=(
                    f"Worker count ({item.workers}) is below the minimum "
                    f"of {rules.min_workers_per_contractor}."
                ),
                severity="warning",
            ))

        # --- Duplicate contractor ---
        if rules.warn_duplicate_contractors and existing_items is not None and item.contractor:
            count = sum(
                1 for i in existing_items
                if i.contractor and i.contractor.strip().lower() == item.contractor.strip().lower()
            )
            if count > 1:
                warnings.append(ValidationWarning(
                    field="contractor",
                    message=(
                        f"Contractor '{item.contractor}' appears {count} times "
                        f"in this report."
                    ),
                    severity="warning",
                ))

        # --- Empty details ---
        if rules.warn_empty_details:
            if not item.details or not item.details.strip():
                warnings.append(ValidationWarning(
                    field="details",
                    message=f"No details provided for '{item.contractor or 'unknown'}'. Consider adding a worker breakdown.",
                    severity="warning",
                ))

        return warnings

    def validate_date(self, date_str: str) -> list[ValidationWarning]:
        """Validate a date string.

        Checks:
        - Valid YYYY-MM-DD format
        - Not before project_start_date
        - Not too far in the future (max_future_days)

        Args:
            date_str: The date string to validate.

        Returns:
            List of ValidationWarning.
        """
        warnings: list[ValidationWarning] = []

        if not date_str or not date_str.strip():
            warnings.append(ValidationWarning(
                field="date",
                message="Date is empty.",
                severity="warning",
            ))
            return warnings

        # Parse the date
        try:
            date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            warnings.append(ValidationWarning(
                field="date",
                message=f"Date '{date_str}' is not a valid YYYY-MM-DD date.",
                severity="warning",
            ))
            return warnings

        rules = self._config.validation

        # Check project start date
        try:
            start_date = datetime.strptime(rules.project_start_date, "%Y-%m-%d").date()
            if date < start_date:
                warnings.append(ValidationWarning(
                    field="date",
                    message=(
                        f"Date {date_str} is before the project start date "
                        f"({rules.project_start_date})."
                    ),
                    severity="warning",
                ))
        except (ValueError, TypeError):
            # If the configured start date is invalid, skip this check quietly
            pass

        # Check future date
        today = datetime.now().date()
        if date > today + timedelta(days=rules.max_future_days):
            warnings.append(ValidationWarning(
                field="date",
                message=(
                    f"Date {date_str} is more than {rules.max_future_days} days "
                    f"in the future."
                ),
                severity="warning",
            ))

        return warnings

    def validate_status_transition(
        self,
        current: Optional[ReportStatus],
        new: ReportStatus,
        user: Optional[str] = None,
        admin: bool = False,
    ) -> list[ValidationWarning]:
        """Check if a status transition is allowed.

        Mirrors ``ReportWorkflowService.VALID_TRANSITIONS``.
        The ReportWorkflowService raises exceptions on invalid transitions;
        this method returns warnings instead, allowing callers to check
        validity without exception handling.

        Args:
            current: Current status of the report.
            new: Desired target status.
            user: Who is performing the action (optional, used for admin check).
            admin: Whether the user is an admin (only relevant for Locked -> Draft).

        Returns:
            List of ValidationWarning (empty = transition is valid).
            'error' severity means the transition should be blocked.
        """
        warnings: list[ValidationWarning] = []

        if current is None:
            warnings.append(ValidationWarning(
                field="status",
                message="Report has no status â€” cannot perform transition.",
                severity="error",
            ))
            return warnings

        allowed = self.VALID_TRANSITIONS.get(current, set())

        if new not in allowed:
            allowed_str = " â†’ ".join(s.value for s in allowed) if allowed else "none"
            warnings.append(ValidationWarning(
                field="status",
                message=(
                    f"Cannot transition from '{current.value}' to '{new.value}'. "
                    f"Allowed transitions from '{current.value}': {allowed_str}."
                ),
                severity="error",
            ))
            return warnings

        # Locked â†’ Draft requires admin
        if current == ReportStatus.LOCKED and new == ReportStatus.DRAFT and not admin:
            warnings.append(ValidationWarning(
                field="status",
                message="Only admins can unlock a report.",
                severity="error",
            ))

        return warnings
