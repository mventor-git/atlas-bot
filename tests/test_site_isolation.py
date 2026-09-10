"""Site isolation tests (ticket-005).

Proves tenant separation at the repository layer:
- Same date may exist once PER SITE (no cross-site duplicate clash).
- Reads default to this bot's SITE_ID; other sites are invisible.
"""

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from app.database.manager import DatabaseManager
from app.models.database import Report, ReportItem, ReportStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.version_repository import VersionRepository
from app.services.contractor_profile_service import ContractorProfileService
from app.repositories.contractor_timeline_repository import (
    ContractorTimelineRepository,
)
from app.repositories.search_repository import SearchRepository
from app.services.universal_search_service import UniversalSearchService
from app.models.search import SearchQuery
from app.utils.exceptions import DatabaseError


@pytest.fixture
def db_path() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "iso.db"


@pytest.fixture
def repos(db_path: Path):
    manager = DatabaseManager(str(db_path))
    yield {
        "reports": ReportRepository(manager),
        "events": EventLogRepository(manager),
        "audit": AuditRepository(manager),
        "versions": None,  # built per-test (needs report repo)
        "manager": manager,
    }
    manager.close_all()


def _report(date: str, site: str) -> Report:
    return Report(date=date, day="Monday", status=ReportStatus.FINAL,
                  telegram_user="u1", site_id=site,
                  items=[ReportItem(contractor="SiteCo", type="Civil",
                                    zone="A", workers=3, details="d")])


class TestSiteIsolation:
    def test_same_date_two_sites_no_clash(self, repos, monkeypatch):
        report_repo = repos["reports"]
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        monkeypatch.setenv("SITE_ID", "site-b")
        # Must NOT raise duplicate: different tenant
        report_repo.add(_report("2026-09-08", "site-b"))

    def test_duplicate_same_site_rejected(self, repos, monkeypatch):
        report_repo = repos["reports"]
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        with pytest.raises(DatabaseError):
            report_repo.add(_report("2026-09-08", "site-a"))

    def test_reads_scoped_to_site(self, repos, monkeypatch):
        report_repo = repos["reports"]
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        monkeypatch.setenv("SITE_ID", "site-b")
        assert report_repo.get_by_date("2026-09-08") is None
        assert report_repo.exists_for_date("2026-09-08") is False
        assert report_repo.get_by_date("2026-09-08", site_id="site-a") is not None
        assert report_repo.get_all() == []
        assert report_repo.count() == 0

    def test_events_stamped_with_site(self, repos, monkeypatch):
        event_repo = repos["events"]
        monkeypatch.setenv("SITE_ID", "site-b")
        entry = event_repo.log(telegram_user="u1", action="test.action")
        assert entry.site_id == "site-b"

    def test_events_scoped_to_site(self, repos, monkeypatch):
        event_repo = repos["events"]
        monkeypatch.setenv("SITE_ID", "site-a")
        event_repo.log(telegram_user="u1", action="test.action")
        monkeypatch.setenv("SITE_ID", "site-b")
        assert event_repo.get_recent() == []
        assert event_repo.get_by_user("u1") == []
        assert event_repo.count() == 0

    def test_audit_scoped_to_site(self, repos, monkeypatch):
        audit_repo = repos["audit"]
        monkeypatch.setenv("SITE_ID", "site-a")
        audit_repo.log_added(telegram_user="u1", user_role="admin",
                             report_date="2026-09-08", report_status="final",
                             contractor_name="SiteCo", workers=3)
        monkeypatch.setenv("SITE_ID", "site-b")
        assert audit_repo.get_by_user("u1") == []
        assert audit_repo.get_recent_by_all_users() == []
        assert audit_repo.get_all_added_values() == []

    def test_timeline_scoped_to_site(self, repos, monkeypatch):
        report_repo = repos["reports"]
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        timeline_repo = ContractorTimelineRepository(repos["manager"])
        monkeypatch.setenv("SITE_ID", "site-b")
        assert timeline_repo.get_all_contractor_dates("SiteCo") == []
        assert timeline_repo.contractor_exists("SiteCo") is False
        result = timeline_repo.get_timeline("SiteCo")
        assert result.total_entries == 0

    def test_profile_scoped_to_site(self, repos, monkeypatch):
        from app.services.contractor_profile_service import ContractorProfileService

        report_repo = repos["reports"]
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        service = ContractorProfileService(repos["manager"])
        monkeypatch.setenv("SITE_ID", "site-b")
        assert service.get_profile("SiteCo") is None

    def test_universal_search_scoped_to_site(self, repos, monkeypatch):
        report_repo = repos["reports"]
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        service = UniversalSearchService(SearchRepository(repos["manager"]))
        monkeypatch.setenv("SITE_ID", "site-b")
        result = service.search(SearchQuery(text="SiteCo"))
        assert result.total_count == 0

    def test_versions_scoped_to_site(self, repos, monkeypatch):
        from app.repositories.version_repository import VersionRepository

        report_repo = repos["reports"]
        monkeypatch.setenv("SITE_ID", "site-a")
        saved = report_repo.add(_report("2026-09-08", "site-a"))
        versions = VersionRepository(repos["manager"], report_repo)
        versions.create_version(saved, telegram_user="u1")
        monkeypatch.setenv("SITE_ID", "site-b")
        assert versions.get_versions(saved.id) == []
        assert versions.get_latest_version(saved.id) is None
        assert versions.count() == 0
