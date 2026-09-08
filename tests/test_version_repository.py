"""
Tests for VersionRepository.

Tests cover:
- Version creation
- Sequential version numbering
- JSON serialization/deserialization
- Version queries (get_versions, get_latest)
- Version restore
- Version pruning
"""

import pytest

from app.models.database import Report, ReportItem, ReportStatus, ReportVersion
from app.repositories.version_repository import VersionRepository
from app.repositories.report_repository import ReportRepository


class TestVersionRepository:
    """Tests for VersionRepository."""

    def setup_method(self):
        """Create a fresh in-memory database for each test."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        from app.database.manager import DatabaseManager
        self._db = DatabaseManager(self._tmp.name)
        self._report_repo = ReportRepository(self._db)
        self._repo = VersionRepository(self._db, self._report_repo)

    def teardown_method(self):
        """Clean up after each test."""
        try:
            self._db.close()
        except Exception:
            pass
        import time
        import os
        for attempt in range(5):
            try:
                os.unlink(self._tmp.name)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1)

    def _create_report(self, date: str = "2026-07-11") -> Report:
        """Helper to create and persist a report with items."""
        report = Report(
            date=date,
            day="السبت",
            status=ReportStatus.FINAL,
            telegram_user="user1",
        )
        report.add_item(ReportItem(contractor="Civil Co", workers=10, zone="Zone A"))
        report.add_item(ReportItem(contractor="Electric Inc", workers=5, zone="Zone B"))
        return self._report_repo.add(report)

    def test_create_version(self):
        """Should create a version with sequential number."""
        report = self._create_report()
        version = self._repo.create_version(report, telegram_user="user1")

        assert version.id is not None, "Version should have an ID"
        assert version.version_number == 1, (
            f"Expected version 1, got {version.version_number}"
        )
        assert version.report_id == report.id, (
            f"Expected report_id {report.id}, got {version.report_id}"
        )

    def test_sequential_version_numbers(self):
        """Versions should be numbered 1, 2, 3..."""
        report = self._create_report()

        v1 = self._repo.create_version(report)
        assert v1.version_number == 1, f"Expected version 1, got {v1.version_number}"

        v2 = self._repo.create_version(report)
        assert v2.version_number == 2, f"Expected version 2, got {v2.version_number}"

        v3 = self._repo.create_version(report)
        assert v3.version_number == 3, f"Expected version 3, got {v3.version_number}"

    def test_snapshot_contains_full_report(self):
        """Version snapshot should contain all report data."""
        report = self._create_report()
        version = self._repo.create_version(report)

        assert version.snapshot is not None, "Snapshot should not be None"
        assert "Civil Co" in version.snapshot, (
            "Snapshot should contain contractor name"
        )
        assert report.date in version.snapshot, (
            "Snapshot should contain report date"
        )
        assert "10" in version.snapshot, (
            "Snapshot should contain worker count"
        )

    def test_get_versions(self):
        """Should return all versions for a report."""
        report = self._create_report()
        self._repo.create_version(report)
        self._repo.create_version(report)

        versions = self._repo.get_versions(report.id)
        assert len(versions) == 2, (
            f"Expected 2 versions, got {len(versions)}"
        )
        assert versions[0].version_number == 1, (
            f"Expected version 1 first, got {versions[0].version_number}"
        )
        assert versions[1].version_number == 2, (
            f"Expected version 2 second, got {versions[1].version_number}"
        )

    def test_get_versions_empty(self):
        """Should return empty list for report with no versions."""
        report = self._create_report()
        versions = self._repo.get_versions(report.id)
        assert versions == [], (
            "Should return empty list for report with no versions"
        )

    def test_get_latest_version(self):
        """Should return the most recent version."""
        report = self._create_report()
        self._repo.create_version(report)
        self._repo.create_version(report)

        latest = self._repo.get_latest_version(report.id)
        assert latest is not None, "Should find latest version"
        assert latest.version_number == 2, (
            f"Expected version 2, got {latest.version_number}"
        )

    def test_get_latest_version_no_versions(self):
        """Should return None if no versions exist."""
        report = self._create_report()
        latest = self._repo.get_latest_version(report.id)
        assert latest is None, (
            "Should return None for report with no versions"
        )

    def test_restore_version_creates_draft(self):
        """Restoring a version should create a draft report."""
        report = self._create_report()
        version = self._repo.create_version(report)
        restored = self._repo.restore_version(version.id, telegram_user="admin")

        assert restored is not None, "Restored report should not be None"
        assert restored.status == ReportStatus.DRAFT, (
            f"Expected DRAFT status, got {restored.status}"
        )
        assert restored.id is None, (
            "Restored report should have no ID (new report)"
        )
        assert len(restored.items) == 2, (
            f"Expected 2 items, got {len(restored.items)}"
        )
        assert restored.items[0].contractor == "Civil Co", (
            f"Expected 'Civil Co', got '{restored.items[0].contractor}'"
        )

    def test_restore_version_preserves_data(self):
        """Restored version should have the same data as original."""
        report = self._create_report()
        version = self._repo.create_version(report)
        restored = self._repo.restore_version(version.id, telegram_user="admin")

        assert restored.date == report.date, (
            f"Date mismatch: {restored.date} vs {report.date}"
        )
        assert restored.day == report.day, (
            f"Day mismatch: {restored.day} vs {report.day}"
        )
        assert restored.source_date == report.date, (
            "source_date should match original date"
        )

    def test_restore_version_not_found(self):
        """Should raise error for non-existent version."""
        from app.utils.exceptions import DatabaseError
        with pytest.raises(DatabaseError, match="not found"):
            self._repo.restore_version(9999, telegram_user="admin")

    def test_create_version_without_id(self):
        """Should raise error if report has no ID."""
        report = Report(date="2026-07-11", day="السبت")
        from app.utils.exceptions import DatabaseError
        with pytest.raises(DatabaseError, match="without an ID"):
            self._repo.create_version(report)

    def test_prune_versions(self):
        """Should remove oldest versions exceeding the limit."""
        report = self._create_report()
        for _ in range(5):
            self._repo.create_version(report)

        assert len(self._repo.get_versions(report.id)) == 5, (
            "Should have 5 versions before pruning"
        )

        pruned = self._repo.prune_versions(report.id, max_versions=3)
        assert pruned == 2, f"Expected 2 pruned, got {pruned}"

        remaining = self._repo.get_versions(report.id)
        assert len(remaining) == 3, (
            f"Expected 3 remaining, got {len(remaining)}"
        )

    def test_prune_versions_below_limit(self):
        """Pruning should not remove versions if under the limit."""
        report = self._create_report()
        self._repo.create_version(report)

        pruned = self._repo.prune_versions(report.id, max_versions=10)
        assert pruned == 0, "Should not prune if under limit"

    def test_count(self):
        """Should return total version count."""
        report = self._create_report()
        assert self._repo.count() == 0, "Should start with 0"

        self._repo.create_version(report)
        assert self._repo.count() == 1, f"Expected 1, got {self._repo.count()}"

    def test_delete_version(self):
        """Should delete a specific version."""
        report = self._create_report()
        version = self._repo.create_version(report)

        deleted = self._repo.delete(version.id)
        assert deleted is True, "Delete should return True"
        assert self._repo.count() == 0, "Should be 0 after delete"
