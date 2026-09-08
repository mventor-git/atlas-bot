"""
Tests for ReportRepository.

Tests cover:
- CRUD operations (add, get_by_id, get_all, update, delete, count)
- Duplicate detection (exists_for_date, get_by_date)
- Replace report
- Date range queries
- No-report dates tracking
- Edge cases (empty items, missing optional fields)
- Error handling
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.report_repository import ReportRepository
from app.utils.exceptions import DatabaseError


class TestReportRepository:
    """Test suite for ReportRepository."""

    @pytest.fixture
    def db_path(self):
        """Create a temporary database file path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir) / "test_reports.db"

    @pytest.fixture
    def db_manager(self, db_path: Path):
        """Create a DatabaseManager instance."""
        manager = DatabaseManager(str(db_path))
        yield manager
        manager.close_all()

    @pytest.fixture
    def repo(self, db_manager: DatabaseManager):
        """Create a ReportRepository instance."""
        return ReportRepository(db_manager)

    @pytest.fixture
    def sample_report(self) -> Report:
        """Create a sample draft report for testing.

        Uses DRAFT status (not GENERATED/FINAL) so that
        update tests pass the lifecycle validation gate.
        """
        report = Report(
            date="2026-07-11",
            day="السبت",
            status=ReportStatus.DRAFT,
            telegram_user="user123",
            created_at="2026-07-11T10:00:00",
        )
        report.add_item(ReportItem(contractor="Civil", type="Civil Type", zone="Zone A", workers=10, details="5 Mason, 5 Helper"))
        report.add_item(ReportItem(contractor="Electrical", type="Electrical Type", zone="Zone B", workers=5, details="3 Electrician, 2 Helper"))
        return report

    # --- Create ---

    def test_add_report(self, repo: ReportRepository, sample_report: Report):
        """Should add a report and return it with ID."""
        result = repo.add(sample_report)
        assert result.id is not None, "Added report should have an ID"
        assert result.date == "2026-07-11", f"Expected date 2026-07-11, got {result.date}"
        assert result.status == ReportStatus.DRAFT, f"Expected status DRAFT, got {result.status}"

    def test_add_report_with_items(self, repo: ReportRepository, sample_report: Report):
        """Should add report items and return them with IDs."""
        result = repo.add(sample_report)
        assert len(result.items) == 2, f"Expected 2 items, got {len(result.items)}"
        for item in result.items:
            assert item.id is not None, "Each item should have an ID"
            assert item.report_id == result.id, f"Expected report_id {result.id}, got {item.report_id}"

    def test_add_no_report(self, repo: ReportRepository):
        """Should add a 'no report' entry."""
        report = Report(
            date="2026-07-11",
            day="السبت",
            status=ReportStatus.NO_REPORT,
            telegram_user="user123",
        )
        result = repo.add(report)
        assert result.id is not None, "Added no-report entry should have an ID"
        assert result.is_no_report, "Report should be marked as NO_REPORT"

    def test_add_duplicate_raises_error(self, repo: ReportRepository, sample_report: Report):
        """Should raise DatabaseError when adding duplicate date."""
        repo.add(sample_report)
        with pytest.raises(DatabaseError, match="already exists"):
            repo.add(sample_report)

    # --- Read ---

    def test_get_by_id(self, repo: ReportRepository, sample_report: Report):
        """Should retrieve a report by ID with items."""
        added = repo.add(sample_report)
        fetched = repo.get_by_id(added.id)
        assert fetched is not None, "Fetched report should not be None"
        assert fetched.id == added.id, f"Expected ID {added.id}, got {fetched.id}"
        assert fetched.date == "2026-07-11", f"Expected date 2026-07-11, got {fetched.date}"
        assert len(fetched.items) == 2, f"Expected 2 items, got {len(fetched.items)}"

    def test_get_by_id_not_found(self, repo: ReportRepository):
        """Should return None for non-existent ID."""
        assert repo.get_by_id(999) is None, "Expected None for non-existent ID"

    def test_get_by_date(self, repo: ReportRepository, sample_report: Report):
        """Should retrieve a report by date."""
        added = repo.add(sample_report)
        fetched = repo.get_by_date("2026-07-11")
        assert fetched is not None, "Fetched report should not be None"
        assert fetched.id == added.id, f"Expected ID {added.id}, got {fetched.id}"

    def test_get_by_date_not_found(self, repo: ReportRepository):
        """Should return None for non-existent date."""
        assert repo.get_by_date("2099-01-01") is None, "Expected None for non-existent date"

    def test_exists_for_date(self, repo: ReportRepository, sample_report: Report):
        """Should check if a report exists for a date."""
        assert not repo.exists_for_date("2026-07-11"), "Report should not exist before adding"
        repo.add(sample_report)
        assert repo.exists_for_date("2026-07-11"), "Report should exist after adding"

    def test_get_all_empty(self, repo: ReportRepository):
        """Should return empty list when no reports exist."""
        assert repo.get_all() == [], f"Expected empty list, got {repo.get_all()}"

    def test_get_all(self, repo: ReportRepository, sample_report: Report):
        """Should return all reports."""
        repo.add(sample_report)
        report2 = Report(date="2026-07-12", day="الأحد")
        repo.add(report2)
        reports = repo.get_all()
        assert len(reports) == 2, f"Expected 2 reports, got {len(reports)}"

    def test_count(self, repo: ReportRepository, sample_report: Report):
        """Should count reports."""
        assert repo.count() == 0, f"Expected count 0, got {repo.count()}"
        repo.add(sample_report)
        assert repo.count() == 1, f"Expected count 1, got {repo.count()}"
        repo.add(Report(date="2026-07-12", day="الأحد"))
        assert repo.count() == 2, f"Expected count 2, got {repo.count()}"

    # --- Update ---

    def test_update_report(self, repo: ReportRepository, sample_report: Report):
        """Should update a report's fields."""
        added = repo.add(sample_report)
        added.pdf_path = "exports/pdf/2026-07-11.pdf"
        added.excel_path = "exports/excel/2026-07-11.xlsx"
        updated = repo.update(added)
        assert updated.pdf_path == "exports/pdf/2026-07-11.pdf", f"Expected 'exports/pdf/2026-07-11.pdf', got '{updated.pdf_path}'"

        fetched = repo.get_by_id(added.id)
        assert fetched is not None, "Fetched report should not be None"
        assert fetched.pdf_path == "exports/pdf/2026-07-11.pdf", f"Expected 'exports/pdf/2026-07-11.pdf', got '{fetched.pdf_path}'"

    def test_update_nonexistent_raises_error(self, repo: ReportRepository):
        """Should raise error when updating report without ID."""
        report = Report(date="2026-07-11", day="السبت")
        with pytest.raises(DatabaseError, match="without an ID"):
            repo.update(report)

    def test_update_replaces_items(self, repo: ReportRepository, sample_report: Report):
        """Should replace all items on update."""
        added = repo.add(sample_report)
        added.items = [ReportItem(contractor="New Contractor", workers=20)]
        repo.update(added)
        fetched = repo.get_by_id(added.id)
        assert fetched is not None, "Fetched report should not be None"
        assert len(fetched.items) == 1, f"Expected 1 item, got {len(fetched.items)}"
        assert fetched.items[0].contractor == "New Contractor", f"Expected 'New Contractor', got '{fetched.items[0].contractor}'"

    # --- Delete ---

    def test_delete_report(self, repo: ReportRepository, sample_report: Report):
        """Should delete a report and its items."""
        added = repo.add(sample_report)
        assert repo.delete(added.id) is True, "First delete should succeed"
        assert repo.get_by_id(added.id) is None, "Report should be None after deletion"

    def test_delete_nonexistent(self, repo: ReportRepository):
        """Should return False when deleting non-existent report."""
        assert repo.delete(999) is False, "Deleting non-existent report should return False"

    def test_delete_cascades_to_items(self, repo: ReportRepository, sample_report: Report):
        """Should delete associated items when deleting a report."""
        added = repo.add(sample_report)
        repo.delete(added.id)

        cursor = repo._db.execute(
            "SELECT COUNT(*) as cnt FROM report_items WHERE report_id = ?",
            (added.id,),
        )
        cnt = cursor.fetchone()["cnt"]
        assert cnt == 0, f"Expected 0 items after cascade delete, got {cnt}"

    # --- Date Range ---

    def test_get_reports_in_range(self, repo: ReportRepository):
        """Should get reports within a date range."""
        repo.add(Report(date="2026-07-10", day="الخميس"))
        repo.add(Report(date="2026-07-11", day="الجمعة"))
        repo.add(Report(date="2026-07-12", day="السبت"))
        repo.add(Report(date="2026-07-15", day="الثلاثاء"))

        result = repo.get_reports_in_range("2026-07-10", "2026-07-12")
        assert len(result) == 3, f"Expected 3 reports, got {len(result)}"
        assert result[0].date == "2026-07-10", f"Expected date 2026-07-10, got {result[0].date}"
        assert result[2].date == "2026-07-12", f"Expected date 2026-07-12, got {result[2].date}"

    def test_get_reports_in_range_empty(self, repo: ReportRepository):
        """Should return empty list for range with no reports."""
        assert repo.get_reports_in_range("2026-01-01", "2026-01-31") == [], "Expected empty list for range with no reports"

    # --- No Report ---

    def test_get_no_report_dates(self, repo: ReportRepository):
        """Should get all no-report dates."""
        repo.add(Report(date="2026-07-10", day="الخميس", status=ReportStatus.NO_REPORT))
        repo.add(Report(date="2026-07-11", day="الجمعة", status=ReportStatus.GENERATED))
        repo.add(Report(date="2026-07-12", day="السبت", status=ReportStatus.NO_REPORT))

        dates = repo.get_no_report_dates()
        assert len(dates) == 2, f"Expected 2 dates, got {len(dates)}"
        assert "2026-07-10" in dates, f"Expected 2026-07-10 in {dates}"
        assert "2026-07-12" in dates, f"Expected 2026-07-12 in {dates}"

    # --- Replace ---

    def test_replace_report(self, repo: ReportRepository, sample_report: Report):
        """Should replace an existing report for the same date."""
        added = repo.add(sample_report)
        original_id = added.id

        new_report = Report(
            date="2026-07-11",
            day="السبت",
            status=ReportStatus.GENERATED,
            telegram_user="new_user",
        )
        new_report.add_item(ReportItem(contractor="New Civil", workers=8))

        replaced = repo.replace_report(new_report)
        assert replaced.id is not None, "Replaced report should have an ID"
        assert replaced.id != original_id, "Replaced report should have a new ID"
        assert replaced.telegram_user == "new_user", f"Expected 'new_user', got '{replaced.telegram_user}'"
        assert len(replaced.items) == 1, f"Expected 1 item, got {len(replaced.items)}"

        assert repo.get_by_id(original_id) is None, "Old report should be gone after replacement"

    def test_replace_non_existing(self, repo: ReportRepository):
        """Should add new report if none exists for date."""
        report = Report(date="2026-07-11", day="السبت")
        replaced = repo.replace_report(report)
        assert replaced.id is not None, "Replaced report should have an ID"
        assert replaced.date == "2026-07-11", f"Expected date 2026-07-11, got {replaced.date}"

    # --- Edge Cases ---

    def test_report_without_items(self, repo: ReportRepository):
        """Should handle reports without items."""
        report = Report(date="2026-07-11", day="السبت", status=ReportStatus.NO_REPORT)
        added = repo.add(report)
        fetched = repo.get_by_id(added.id)
        assert fetched is not None, "Fetched report should not be None"
        assert fetched.items == [], f"Expected empty items, got {fetched.items}"

    def test_report_with_minimal_fields(self, repo: ReportRepository):
        """Should handle reports with only required fields."""
        report = Report(date="2026-07-11", day="السبت")
        added = repo.add(report)
        assert added.id is not None, "Added report should have an ID"

    def test_report_with_nullable_fields(self, repo: ReportRepository):
        """Should handle reports with None in optional fields."""
        report = Report(date="2026-07-11", day="السبت")
        item = ReportItem(contractor="Test", type=None, zone=None, workers=None, details=None)
        report.add_item(item)
        added = repo.add(report)
        assert added.id is not None, "Added report should have an ID"
        assert added.items[0].type is None, f"Expected type to be None, got {added.items[0].type}"

    # --- New Edge Cases ---

    def test_get_by_id_none(self, repo: ReportRepository):
        """Should handle None as ID."""
        result = repo.get_by_id(None)  # type: ignore
        assert result is None, f"Expected None for ID=None, got {result}"

    def test_get_by_date_empty_string(self, repo: ReportRepository):
        """Should return None for empty date string."""
        result = repo.get_by_date("")
        assert result is None, f"Expected None for empty date, got {result}"

    def test_get_reports_in_range_same_date(self, repo: ReportRepository):
        """Should find reports when start and end are the same date."""
        report = Report(date="2026-07-11", day="السبت")
        saved = repo.add(report)
        results = repo.get_reports_in_range("2026-07-11", "2026-07-11")
        assert len(results) == 1, f"Expected 1 report, got {len(results)}"
        assert results[0].id == saved.id, f"Expected ID {saved.id}, got {results[0].id}"

    def test_get_reports_in_range_reversed_dates(self, repo: ReportRepository):
        """Should return empty when start > end."""
        results = repo.get_reports_in_range("2026-07-12", "2026-07-10")
        assert results == [], f"Expected empty list for reversed dates, got {len(results)} reports"

    def test_delete_twice(self, repo: ReportRepository):
        """Second delete should return False."""
        report = Report(date="2026-07-11", day="السبت")
        saved = repo.add(report)
        first = repo.delete(saved.id)
        assert first is True, "First delete should succeed"
        second = repo.delete(saved.id)
        assert second is False, "Second delete should return False"

    def test_add_duplicate_with_different_status(self, repo: ReportRepository):
        """Should raise for any duplicate date regardless of status."""
        repo.add(Report(date="2026-07-11", day="السبت", status=ReportStatus.GENERATED))
        with pytest.raises(DatabaseError):
            repo.add(Report(date="2026-07-11", day="السبت", status=ReportStatus.NO_REPORT))

    def test_get_no_report_dates_empty(self, repo: ReportRepository):
        """Should return empty list when no NO_REPORT exists."""
        results = repo.get_no_report_dates()
        assert results == [], f"Expected empty list, got {results}"
