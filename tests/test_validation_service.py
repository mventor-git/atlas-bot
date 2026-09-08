"""
Tests for ValidationService. (mventor-ticket-013)

Covers:
- High worker count warning
- Low worker count warning
- Duplicate contractor warning
- Empty contractor name warning
- Empty day warning
- Empty date warning
- Invalid date format warning
- Date before project start warning
- Date too far in future warning
- Too many contractors warning
- Empty details warning
- No warnings for valid data
- Status transition validation (valid + invalid)
- Admin unlock requirement
- Custom thresholds from config
"""

from datetime import datetime, timedelta

import pytest

from app.models.config import AppConfig
from app.models.database import Report, ReportItem, ReportStatus
from app.services.validation_service import ValidationService, ValidationWarning


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides) -> AppConfig:
    """Create an AppConfig with default values, merged with overrides."""
    defaults = {
        "template": {"file": "t.xlsx", "tables_file": "database/tables.xlsx"},
        "date": {"cell": "B4", "day_cell": "D4"},
        "table": {"start_row": 12, "columns": {"serial": "A", "contractor": "B", "type": "C", "zone": "D", "workers": "E", "details": "F"}},
        "output": {"pdf_folder": "exports", "excel_folder": "exports"},
        "database": {"path": "test.db"},
        "history": {"file": "h.xlsx"},
        "logging": {"file": "l.log", "level": "INFO", "max_bytes": 1024, "backup_count": 1},
        "tables": {"contractor_sheet": "tblContractor", "zones_sheet": "tblZones"},
    }
    # Merge validation overrides
    if "validation" in overrides:
        defaults["validation"] = overrides.pop("validation")
    cfg = AppConfig(**defaults, **overrides)
    return cfg


def make_service(**overrides) -> ValidationService:
    """Create a ValidationService with optional config overrides."""
    return ValidationService(make_config(**overrides))


def make_item(contractor: str = "Civil Co", workers: int = 10,
              details: str = "5 Mason, 5 Helper") -> ReportItem:
    return ReportItem(contractor=contractor, workers=workers, details=details)


def make_report(items: list[ReportItem] | None = None,
                date: str = "2026-07-11",
                day: str = "Ø§Ù„Ø³Ø¨Øª") -> Report:
    report = Report(date=date, day=day)
    if items:
        for item in items:
            report.add_item(item)
    return report


# ===================================================================
# ValidationWarning
# ===================================================================

class TestValidationWarning:
    """Tests for the ValidationWarning dataclass."""

    def test_creation(self):
        w = ValidationWarning(field="workers", message="Too many!", severity="warning")
        assert w.field == "workers"
        assert w.message == "Too many!"
        assert w.severity == "warning"

    def test_default_severity(self):
        w = ValidationWarning(field="date", message="Bad date")
        assert w.severity == "warning"

    def test_repr(self):
        w = ValidationWarning(field="x", message="y", severity="error")
        r = repr(w)
        assert "field='x'" in r
        assert "message='y'" in r
        assert "severity='error'" in r

    def test_equality(self):
        a = ValidationWarning("f", "m", "warning")
        b = ValidationWarning("f", "m", "warning")
        c = ValidationWarning("f", "different", "warning")
        assert a == b
        assert a != c
        assert a != "not a warning"


# ===================================================================
# Item validation
# ===================================================================

class TestValidateItem:
    """Tests for validate_item()."""

    def test_valid_item_no_warnings(self):
        svc = make_service()
        item = make_item()
        warnings = svc.validate_item(item)
        assert len(warnings) == 0, f"Expected no warnings, got {warnings}"

    def test_high_worker_count(self):
        svc = make_service(validation={"max_workers_per_contractor": 50})
        item = make_item(workers=100)
        warnings = svc.validate_item(item)
        assert len(warnings) >= 1
        assert any(
            w.field == "workers" and "exceeds" in w.message
            for w in warnings
        )

    def test_high_worker_count_default_threshold(self):
        svc = make_service()  # default max_workers = 100
        item = make_item(workers=101)
        warnings = svc.validate_item(item)
        assert any(w.field == "workers" for w in warnings)

    def test_at_max_workers_no_warning(self):
        svc = make_service(validation={"max_workers_per_contractor": 100})
        item = make_item(workers=100)
        warnings = svc.validate_item(item)
        assert not any(w.field == "workers" for w in warnings)

    def test_low_worker_count(self):
        svc = make_service(validation={"min_workers_per_contractor": 2})
        item = make_item(workers=1)
        warnings = svc.validate_item(item)
        assert any(
            w.field == "workers" and "below" in w.message
            for w in warnings
        )

    def test_low_worker_count_default(self):
        svc = make_service()  # default min_workers = 1
        item = make_item(workers=0)
        warnings = svc.validate_item(item)
        assert any(w.field == "workers" for w in warnings)

    def test_worker_count_zero_at_minimum(self):
        svc = make_service(validation={"min_workers_per_contractor": 0})
        item = make_item(workers=0)
        warnings = svc.validate_item(item)
        assert not any(
            w.field == "workers" and "below" in w.message
            for w in warnings
        )

    def test_duplicate_contractor_warning(self):
        svc = make_service()
        item_a = make_item(contractor="Civil Co", workers=5)
        item_b = make_item(contractor="Civil Co", workers=3)
        items = [item_a, item_b]
        # Validate item_b against all items (duplicate detected)
        warnings = svc.validate_item(item_b, existing_items=items)
        assert any(
            w.field == "contractor" and "appears" in w.message
            for w in warnings
        )

    def test_duplicate_contractor_case_insensitive(self):
        svc = make_service()
        item_a = make_item(contractor="Civil Co", workers=5)
        item_b = make_item(contractor="civil co", workers=3)
        items = [item_a, item_b]
        warnings = svc.validate_item(item_b, existing_items=items)
        assert any(
            w.field == "contractor" and "appears" in w.message
            for w in warnings
        )

    def test_no_duplicate_warning_for_first_occurrence(self):
        svc = make_service()
        item = make_item(contractor="Civil Co")
        items = [item]
        warnings = svc.validate_item(item, existing_items=items)
        # First occurrence should not trigger duplicate warning
        assert not any(
            w.field == "contractor" and "appears" in w.message
            for w in warnings
        )

    def test_duplicate_check_skipped_when_disabled(self):
        svc = make_service(validation={"warn_duplicate_contractors": False})
        item_a = make_item(contractor="Civil Co")
        item_b = make_item(contractor="Civil Co")
        items = [item_a, item_b]
        warnings = svc.validate_item(item_b, existing_items=items)
        assert not any(
            w.field == "contractor" and "appears" in w.message
            for w in warnings
        )

    def test_empty_contractor_name(self):
        svc = make_service()
        item = make_item(contractor="", workers=5)
        warnings = svc.validate_item(item)
        assert any(
            w.field == "contractor" and "empty" in w.message.lower()
            for w in warnings
        )

    def test_whitespace_contractor_name(self):
        svc = make_service()
        item = make_item(contractor="   ", workers=5)
        warnings = svc.validate_item(item)
        assert any(
            w.field == "contractor" and "empty" in w.message.lower()
            for w in warnings
        )

    def test_empty_details_warning(self):
        svc = make_service()
        item = make_item(details="")
        warnings = svc.validate_item(item)
        assert any(
            w.field == "details" for w in warnings
        )

    def test_empty_details_skipped_when_disabled(self):
        svc = make_service(validation={"warn_empty_details": False})
        item = make_item(details="")
        warnings = svc.validate_item(item)
        assert not any(w.field == "details" for w in warnings)

    def test_no_warning_when_details_provided(self):
        svc = make_service()
        item = make_item(details="10 Mason, 5 Helper")
        warnings = svc.validate_item(item)
        assert not any(w.field == "details" for w in warnings)

    def test_workers_none_does_not_trigger_count_warnings(self):
        svc = make_service()
        item = make_item(workers=None)
        warnings = svc.validate_item(item)
        assert not any(w.field == "workers" for w in warnings)

    def test_multiple_warnings_on_same_item(self):
        """An item with multiple issues should return multiple warnings."""
        svc = make_service(validation={
            "max_workers_per_contractor": 50,
            "min_workers_per_contractor": 2,
        })
        item = make_item(contractor="", workers=200, details="")
        existing = [item]  # duplicate of itself
        warnings = svc.validate_item(item, existing_items=existing)
        fields = {w.field for w in warnings}
        assert "contractor" in fields  # empty name
        assert "workers" in fields     # exceeds max
        assert "details" in fields     # empty details


# ===================================================================
# Report validation
# ===================================================================

class TestValidateReport:
    """Tests for validate_report()."""

    def test_valid_report_no_warnings(self):
        svc = make_service()
        report = make_report(items=[make_item()])
        warnings = svc.validate_report(report)
        assert len(warnings) == 0, f"Expected no warnings, got {warnings}"

    def test_empty_day_warning(self):
        svc = make_service()
        report = make_report(items=[make_item()], day="")
        warnings = svc.validate_report(report)
        assert any(w.field == "day" for w in warnings)

    def test_too_many_contractors(self):
        svc = make_service(validation={"max_contractors_per_report": 2})
        items = [make_item(contractor=f"Contractor {i}") for i in range(5)]
        report = make_report(items=items)
        warnings = svc.validate_report(report)
        assert any(
            w.field == "contractors" and "exceeds" in w.message
            for w in warnings
        )

    def test_too_many_contractors_at_limit(self):
        """Exactly at the limit should not warn."""
        svc = make_service(validation={"max_contractors_per_report": 3})
        items = [make_item(contractor=f"C{i}") for i in range(3)]
        report = make_report(items=items)
        warnings = svc.validate_report(report)
        assert not any(w.field == "contractors" for w in warnings)

    def test_report_with_multiple_issues(self):
        svc = make_service(validation={
            "max_contractors_per_report": 1,
            "min_workers_per_contractor": 2,
        })
        items = [
            make_item(contractor="C1", workers=1),
            make_item(contractor="C2", workers=200),
        ]
        report = make_report(items=items, day="")
        warnings = svc.validate_report(report)
        # Should have: empty day + too many contractors + low workers + high workers
        fields = {w.field for w in warnings}
        assert "day" in fields
        assert "contractors" in fields  # too many
        assert "workers" in fields      # low + high


# ===================================================================
# Date validation
# ===================================================================

class TestValidateDate:
    """Tests for validate_date()."""

    def test_valid_date_no_warnings(self):
        svc = make_service()
        warnings = svc.validate_date("2026-07-11")
        assert len(warnings) == 0

    def test_empty_date(self):
        svc = make_service()
        warnings = svc.validate_date("")
        assert any(w.field == "date" for w in warnings)

    def test_whitespace_date(self):
        svc = make_service()
        warnings = svc.validate_date("   ")
        assert any(w.field == "date" for w in warnings)

    def test_invalid_format(self):
        svc = make_service()
        warnings = svc.validate_date("11-07-2026")
        assert any(w.field == "date" for w in warnings)

    def test_garbage_string(self):
        svc = make_service()
        warnings = svc.validate_date("not-a-date")
        assert any(w.field == "date" for w in warnings)

    def test_date_before_project_start(self):
        svc = make_service(validation={"project_start_date": "2025-06-01"})
        warnings = svc.validate_date("2024-12-31")
        assert any(
            w.field == "date" and "before" in w.message
            for w in warnings
        )

    def test_date_after_project_start_no_warning(self):
        svc = make_service(validation={"project_start_date": "2025-01-01"})
        warnings = svc.validate_date("2025-06-15")
        assert not any(w.field == "date" for w in warnings)

    def test_date_too_far_in_future(self):
        svc = make_service(validation={"max_future_days": 7})
        far_future = (datetime.now().date() + timedelta(days=30)).isoformat()
        warnings = svc.validate_date(far_future)
        assert any(
            w.field == "date" and "future" in w.message
            for w in warnings
        )

    def test_date_within_future_limit_no_warning(self):
        svc = make_service(validation={"max_future_days": 30})
        near_future = (datetime.now().date() + timedelta(days=7)).isoformat()
        warnings = svc.validate_date(near_future)
        assert not any(
            w.field == "date" and "future" in w.message
            for w in warnings
        )

    def test_past_date_no_warning(self):
        svc = make_service(validation={"project_start_date": "2020-01-01"})
        warnings = svc.validate_date("2026-06-01")
        assert not any(w.field == "date" for w in warnings)

    def test_today_no_warning(self):
        svc = make_service()
        today = datetime.now().date().isoformat()
        warnings = svc.validate_date(today)
        assert not any(w.field == "date" for w in warnings)


# ===================================================================
# Status transition validation
# ===================================================================

class TestValidateStatusTransition:
    """Tests for validate_status_transition()."""

    def test_draft_to_final_valid(self):
        svc = make_service()
        warnings = svc.validate_status_transition(ReportStatus.DRAFT, ReportStatus.FINAL)
        assert len(warnings) == 0

    def test_final_to_locked_valid(self):
        svc = make_service()
        warnings = svc.validate_status_transition(ReportStatus.FINAL, ReportStatus.LOCKED)
        assert len(warnings) == 0

    def test_locked_to_draft_admin_valid(self):
        svc = make_service()
        warnings = svc.validate_status_transition(
            ReportStatus.LOCKED, ReportStatus.DRAFT, admin=True
        )
        assert len(warnings) == 0

    def test_locked_to_draft_non_admin_error(self):
        svc = make_service()
        warnings = svc.validate_status_transition(
            ReportStatus.LOCKED, ReportStatus.DRAFT, admin=False
        )
        assert len(warnings) == 1
        assert warnings[0].severity == "error"
        assert "admin" in warnings[0].message.lower()

    def test_draft_to_locked_invalid(self):
        svc = make_service()
        warnings = svc.validate_status_transition(ReportStatus.DRAFT, ReportStatus.LOCKED)
        assert len(warnings) >= 1
        assert warnings[0].severity == "error"

    def test_final_to_draft_invalid(self):
        svc = make_service()
        warnings = svc.validate_status_transition(ReportStatus.FINAL, ReportStatus.DRAFT)
        assert len(warnings) >= 1
        assert warnings[0].severity == "error"

    def test_locked_to_final_invalid(self):
        svc = make_service()
        warnings = svc.validate_status_transition(ReportStatus.LOCKED, ReportStatus.FINAL)
        assert len(warnings) >= 1
        assert warnings[0].severity == "error"

    def test_none_status_error(self):
        svc = make_service()
        warnings = svc.validate_status_transition(None, ReportStatus.FINAL)
        assert len(warnings) >= 1
        assert warnings[0].severity == "error"

    def test_all_valid_transitions(self):
        """All documented valid transitions should pass."""
        svc = make_service()
        assert svc.validate_status_transition(ReportStatus.DRAFT, ReportStatus.FINAL) == []
        assert svc.validate_status_transition(ReportStatus.FINAL, ReportStatus.LOCKED) == []
        assert svc.validate_status_transition(
            ReportStatus.LOCKED, ReportStatus.DRAFT, admin=True
        ) == []
