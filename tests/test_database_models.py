"""
Tests for database models (Report, ReportItem, ReportStatus).

Tests cover:
- Report dataclass creation and defaults
- ReportItem dataclass creation
- ReportStatus enum values
- Report helper properties (is_generated, is_no_report)
- Report.add_item method
- Edge cases (empty items, no date, etc.)
"""

from datetime import datetime

from app.models.database import Report, ReportItem, ReportStatus


class TestReportStatus:
    """Tests for the ReportStatus enum.

    v2.0: Status extended from (generated, no_report) to
    (draft, final, locked, no_report). GENERATED kept as legacy alias for FINAL.
    """

    def test_draft_value(self):
        assert ReportStatus.DRAFT.value == "draft", (
            f"Expected 'draft', got '{ReportStatus.DRAFT.value}'"
        )

    def test_final_value(self):
        assert ReportStatus.FINAL.value == "final", (
            f"Expected 'final', got '{ReportStatus.FINAL.value}'"
        )

    def test_locked_value(self):
        assert ReportStatus.LOCKED.value == "locked", (
            f"Expected 'locked', got '{ReportStatus.LOCKED.value}'"
        )

    def test_no_report_value(self):
        assert ReportStatus.NO_REPORT.value == "no_report", (
            f"Expected 'no_report', got '{ReportStatus.NO_REPORT.value}'"
        )

    def test_generated_is_alias_for_final(self):
        """GENERATED is a legacy alias that maps to FINAL value."""
        assert ReportStatus.GENERATED == ReportStatus.FINAL, (
            f"GENERATED should equal FINAL, got {ReportStatus.GENERATED} vs {ReportStatus.FINAL}"
        )
        assert ReportStatus.GENERATED.value == "final", (
            f"GENERATED.value should be 'final', got '{ReportStatus.GENERATED.value}'"
        )

    def test_enum_membership(self):
        values = [s.value for s in ReportStatus]
        expected_values = ["draft", "final", "locked", "no_report"]
        for expected in expected_values:
            assert expected in values, (
                f"'{expected}' not found in enum values: {values}"
            )

    def test_all_values_unique(self):
        """All enum values should be unique (aliases excluded)."""
        values = set(s.value for s in ReportStatus)
        assert len(values) == 6, (
            f"Expected 6 unique values, got {len(values)}: {values}"
        )


class TestReportItem:
    """Tests for the ReportItem dataclass."""

    def test_minimal_item(self):
        item = ReportItem(contractor="Test Contractor")
        assert item.contractor == "Test Contractor", (
            f"Expected 'Test Contractor', got '{item.contractor}'"
        )
        assert item.type is None, f"Expected type=None, got '{item.type}'"
        assert item.zone is None, f"Expected zone=None, got '{item.zone}'"
        assert item.workers is None, (
            f"Expected workers=None, got {item.workers}"
        )
        assert item.details is None, (
            f"Expected details=None, got '{item.details}'"
        )
        assert item.id is None, f"Expected id=None, got {item.id}"
        assert item.report_id is None, (
            f"Expected report_id=None, got {item.report_id}"
        )

    def test_full_item(self):
        item = ReportItem(
            contractor="Civil Contractor",
            type="Civil",
            zone="Zone A",
            workers=15,
            details="10 Mason, 3 Helper, 2 Carpenter",
            id=1,
            report_id=5,
        )
        assert item.contractor == "Civil Contractor", (
            f"Expected 'Civil Contractor', got '{item.contractor}'"
        )
        assert item.type == "Civil", (
            f"Expected type='Civil', got '{item.type}'"
        )
        assert item.zone == "Zone A", (
            f"Expected zone='Zone A', got '{item.zone}'"
        )
        assert item.workers == 15, (
            f"Expected workers=15, got {item.workers}"
        )
        assert item.details == "10 Mason, 3 Helper, 2 Carpenter", (
            f"Expected details='10 Mason, 3 Helper, 2 Carpenter', got '{item.details}'"
        )
        assert item.id == 1, f"Expected id=1, got {item.id}"
        assert item.report_id == 5, (
            f"Expected report_id=5, got {item.report_id}"
        )

    def test_item_with_workers_zero(self):
        """Should handle workers=0."""
        item = ReportItem(contractor="Test Co", workers=0)
        assert item.workers == 0, (
            f"Expected workers=0, got {item.workers}"
        )


class TestReport:
    """Tests for the Report dataclass."""

    def test_minimal_report(self):
        report = Report(date="2026-07-11", day="السبت")
        assert report.date == "2026-07-11", (
            f"Expected date='2026-07-11', got '{report.date}'"
        )
        assert report.day == "السبت", (
            f"Expected day='السبت', got '{report.day}'"
        )
        # v2.0: Default status is DRAFT (not GENERATED)
        assert report.status == ReportStatus.DRAFT, (
            f"Expected status=DRAFT, got {report.status}"
        )
        assert report.telegram_user is None, (
            f"Expected telegram_user=None, got '{report.telegram_user}'"
        )
        assert report.pdf_path is None, (
            f"Expected pdf_path=None, got '{report.pdf_path}'"
        )
        assert report.excel_path is None, (
            f"Expected excel_path=None, got '{report.excel_path}'"
        )
        assert report.id is None, f"Expected id=None, got {report.id}"
        assert report.items == [], (
            f"Expected items=[], got {report.items}"
        )

    def test_draft_status(self):
        """Default report should be DRAFT with is_draft=True, is_generated=False."""
        report = Report(date="2026-07-11", day="السبت")
        assert report.is_draft is True, (
            "Expected is_draft=True for default report"
        )
        assert report.is_generated is False, (
            "Expected is_generated=False for DRAFT report"
        )
        assert report.is_no_report is False, (
            "Expected is_no_report=False for default report"
        )
        assert report.is_editable is True, (
            "Expected is_editable=True for DRAFT report"
        )

    def test_final_status(self):
        """FINAL report should have is_generated=True, is_editable=False."""
        report = Report(date="2026-07-11", day="السبت", status=ReportStatus.FINAL)
        assert report.is_final is True, (
            "Expected is_final=True for FINAL report"
        )
        assert report.is_generated is True, (
            "Expected is_generated=True for FINAL report"
        )
        assert report.is_editable is False, (
            "Expected is_editable=False for FINAL report"
        )

    def test_no_report_status(self):
        report = Report(
            date="2026-07-11", day="السبت", status=ReportStatus.NO_REPORT
        )
        assert report.is_generated is False, (
            "Expected is_generated=False for NO_REPORT status"
        )
        assert report.is_no_report is True, (
            "Expected is_no_report=True for NO_REPORT status"
        )

    def test_add_item(self):
        report = Report(date="2026-07-11", day="السبت")
        item = ReportItem(contractor="Civil", workers=10)
        report.add_item(item)
        assert len(report.items) == 1, (
            f"Expected 1 item, got {len(report.items)}"
        )
        assert report.items[0] is item, (
            "Added item is not the same object as retrieved"
        )

    def test_add_multiple_items(self):
        report = Report(date="2026-07-11", day="السبت")
        report.add_item(ReportItem(contractor="Civil", workers=10))
        report.add_item(ReportItem(contractor="Electrical", workers=5))
        assert len(report.items) == 2, (
            f"Expected 2 items, got {len(report.items)}"
        )

    def test_created_at_default(self):
        report = Report(date="2026-07-11", day="السبت")
        assert report.created_at is not None, (
            "created_at should not be None for a new report"
        )
        datetime.fromisoformat(report.created_at)

    def test_full_report_creation(self):
        report = Report(
            date="2026-07-11",
            day="السبت",
            status=ReportStatus.FINAL,
            telegram_user="user123",
            pdf_path="exports/pdf/2026-07-11.pdf",
            excel_path="exports/excel/2026-07-11.xlsx",
            created_at="2026-07-11T10:00:00",
            id=1,
        )
        report.add_item(ReportItem(contractor="Civil", workers=10))
        report.add_item(ReportItem(contractor="Electrical", workers=5))

        assert report.id == 1, f"Expected id=1, got {report.id}"
        assert report.telegram_user == "user123", (
            f"Expected telegram_user='user123', got '{report.telegram_user}'"
        )
        assert len(report.items) == 2, (
            f"Expected 2 items, got {len(report.items)}"
        )

    def test_report_with_telegram_user(self):
        report = Report(
            date="2026-07-11", day="السبت", telegram_user="telegram_12345"
        )
        assert report.telegram_user == "telegram_12345", (
            f"Expected telegram_user='telegram_12345', got '{report.telegram_user}'"
        )

    def test_report_with_empty_date_string(self):
        """Should handle empty date string. Default status is DRAFT."""
        report = Report(date="", day="السبت")
        assert report.date == "", (
            f"Expected empty date, got '{report.date}'"
        )
        assert report.status == ReportStatus.DRAFT, (
            f"Expected default status DRAFT, got {report.status}"
        )
        assert report.is_draft is True, (
            "Empty date report should default to DRAFT"
        )

    def test_add_item_none_raises(self):
        """add_item should accept None (appended to list)."""
        report = Report(date="2026-07-11", day="السبت")
        report.add_item(None)  # type: ignore
        assert report.items == [None], (
            f"Expected [None], got {report.items}"
        )
