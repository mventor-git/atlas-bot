"""
Tests for OneClickYesterdayService. (mventor-ticket-010)

Covers:
- Copy yesterday's full report successfully
- Yesterday's report unchanged after copy
- New draft has today's date
- All contractors, workers, zones, details copied
- source_date correctly set to yesterday
- Handle missing yesterday (no report) -> None
- Handle no_report yesterday -> None
- Empty items on yesterday -> empty items on today
- Report type and zone preserved correctly
"""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.services.one_click_yesterday_service import OneClickYesterdayService


class TestOneClickYesterdayService:
    """Test suite for OneClickYesterdayService."""

    @pytest.fixture
    def db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_oneyest.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        return ReportRepository(db_manager)

    @pytest.fixture
    def service(self, repo: ReportRepository):
        return OneClickYesterdayService(repo)

    @pytest.fixture
    def yesterday_date(self) -> str:
        return "2026-07-10"

    @pytest.fixture
    def today_date(self) -> str:
        return "2026-07-11"

    @pytest.fixture
    def today_day(self) -> str:
        return "Ø§Ù„Ø³Ø¨Øª"

    # ------------------------------------------------------------------
    # Successful copy
    # ------------------------------------------------------------------

    def test_copy_yesterday_full_report(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Should copy all items from yesterday's report to today's draft."""
        # Create yesterday's report with items
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL, telegram_user="user1",
        )
        yesterday.add_item(ReportItem(
            contractor="Civil Co", type="Civil", zone="Zone A",
            workers=10, details="5 Mason, 5 Helper",
        ))
        yesterday.add_item(ReportItem(
            contractor="Electric Inc", type="Electrical", zone="Zone B",
            workers=5, details="3 Electrician, 2 Helper",
        ))
        repo.add(yesterday)

        # Copy
        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="user2",
        )

        assert draft is not None, "Should return a draft"
        assert draft.date == today_date, (
            f"Expected date {today_date}, got {draft.date}"
        )
        assert draft.day == today_day, (
            f"Expected day {today_day}, got {draft.day}"
        )
        assert draft.status == ReportStatus.DRAFT
        assert draft.telegram_user == "user2"
        assert draft.source_date == yesterday_date
        assert len(draft.items) == 2, (
            f"Expected 2 items, got {len(draft.items)}"
        )

    def test_all_fields_copied(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """All item fields (contractor, type, zone, workers, details) should be copied."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL, telegram_user="u1",
        )
        yesterday.add_item(ReportItem(
            contractor="Steel Works", type="Steel", zone="Zone C",
            workers=8, details="6 Welder, 2 Helper",
            contractor_code="STL-001",
        ))
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )

        assert draft is not None
        item = draft.items[0]
        assert item.contractor == "Steel Works"
        assert item.type == "Steel"
        assert item.zone == "Zone C"
        assert item.workers == 8
        assert item.details == "6 Welder, 2 Helper"
        assert item.contractor_code == "STL-001"

    def test_yesterday_report_unchanged(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Yesterday's report in the database should NOT be modified."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL, telegram_user="u1",
        )
        yesterday.add_item(ReportItem(contractor="Civil Co", workers=10))
        saved = repo.add(yesterday)

        # Copy
        service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )

        # Verify yesterday unchanged
        still = repo.get_by_id(saved.id)
        assert still is not None
        assert still.status == ReportStatus.FINAL
        assert still.date == yesterday_date
        assert len(still.items) == 1

    def test_new_draft_has_no_id(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """The new draft should have no ID (not yet persisted)."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL, telegram_user="u1",
        )
        yesterday.add_item(ReportItem(contractor="Civil Co", workers=10))
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )
        assert draft is not None
        assert draft.id is None, "Draft should not have an ID (not persisted)"

    # ------------------------------------------------------------------
    # Empty / edge cases
    # ------------------------------------------------------------------

    def test_no_yesterday_report_returns_none(
        self, service: OneClickYesterdayService,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Should return None when yesterday has no report."""
        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u1",
        )
        assert draft is None, (
            "Should return None when no yesterday report exists"
        )

    def test_no_report_status_returns_none(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Should return None when yesterday is a no_report day."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.NO_REPORT, telegram_user="u1",
        )
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u1",
        )
        assert draft is None, (
            "Should return None for no_report yesterday"
        )

    def test_empty_items_on_yesterday(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Should copy empty items list when yesterday has no items."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )
        assert draft is not None
        assert len(draft.items) == 0, "Should have empty items list"

    def test_draft_yesterday_copied(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Should copy from a DRAFT yesterday (not just FINAL)."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.DRAFT, telegram_user="u1",
        )
        yesterday.add_item(ReportItem(contractor="Draft Co", workers=3))
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )
        assert draft is not None
        assert len(draft.items) == 1
        assert draft.items[0].contractor == "Draft Co"

    def test_locked_yesterday_copied(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Should copy from a LOCKED yesterday (read-only is fine for copy)."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.LOCKED, telegram_user="u1",
            locked_by="admin",
        )
        yesterday.add_item(ReportItem(contractor="Locked Co", workers=7))
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )
        assert draft is not None
        assert len(draft.items) == 1
        assert draft.items[0].contractor == "Locked Co"

    # ------------------------------------------------------------------
    # Multiple items and edge cases
    # ------------------------------------------------------------------

    def test_multiple_items_copied_in_order(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Items should be copied in the same order as yesterday."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL, telegram_user="u1",
        )
        contractors = ["Alpha Co", "Beta Inc", "Gamma LLC", "Delta Corp"]
        for i, name in enumerate(contractors):
            yesterday.add_item(ReportItem(
                contractor=name, workers=(i + 1) * 5,
            ))
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )
        assert draft is not None
        copied_names = [item.contractor for item in draft.items]
        assert copied_names == contractors, (
            f"Expected order {contractors}, got {copied_names}"
        )

    def test_copy_with_nullable_fields_null(
        self, service: OneClickYesterdayService, repo: ReportRepository,
        yesterday_date: str, today_date: str, today_day: str,
    ):
        """Should handle items with None fields gracefully."""
        yesterday = Report(
            date=yesterday_date, day="Ø§Ù„Ø®Ù…ÙŠØ³",
            status=ReportStatus.FINAL, telegram_user="u1",
        )
        yesterday.add_item(ReportItem(
            contractor="Minimal Co",
            type=None, zone=None, workers=None, details=None,
            contractor_code=None,
        ))
        repo.add(yesterday)

        draft = service.copy_yesterday(
            yesterday_date, today_date, today_day, telegram_user="u2",
        )
        assert draft is not None
        assert len(draft.items) == 1
        item = draft.items[0]
        assert item.contractor == "Minimal Co"
        assert item.type is None
        assert item.zone is None
        assert item.workers is None
        assert item.details is None
