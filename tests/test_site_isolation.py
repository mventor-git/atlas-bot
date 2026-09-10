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
from app.models.database import Report, ReportStatus
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.report_repository import ReportRepository
from app.utils.exceptions import DatabaseError


@pytest.fixture
def db_path() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "iso.db"


@pytest.fixture
def repos(db_path: Path):
    manager = DatabaseManager(str(db_path))
    yield ReportRepository(manager), EventLogRepository(manager)
    manager.close_all()


def _report(date: str, site: str) -> Report:
    return Report(date=date, day="Monday", status=ReportStatus.DRAFT,
                  telegram_user="u1", site_id=site)


class TestSiteIsolation:
    def test_same_date_two_sites_no_clash(self, repos, monkeypatch):
        report_repo, _ = repos
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        monkeypatch.setenv("SITE_ID", "site-b")
        # Must NOT raise duplicate: different tenant
        report_repo.add(_report("2026-09-08", "site-b"))

    def test_duplicate_same_site_rejected(self, repos, monkeypatch):
        report_repo, _ = repos
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        with pytest.raises(DatabaseError):
            report_repo.add(_report("2026-09-08", "site-a"))

    def test_reads_scoped_to_site(self, repos, monkeypatch):
        report_repo, _ = repos
        monkeypatch.setenv("SITE_ID", "site-a")
        report_repo.add(_report("2026-09-08", "site-a"))
        monkeypatch.setenv("SITE_ID", "site-b")
        assert report_repo.get_by_date("2026-09-08") is None
        assert report_repo.exists_for_date("2026-09-08") is False
        assert report_repo.get_by_date("2026-09-08", site_id="site-a") is not None

    def test_events_stamped_with_site(self, repos, monkeypatch):
        _, event_repo = repos
        monkeypatch.setenv("SITE_ID", "site-b")
        entry = event_repo.log(telegram_user="u1", action="test.action")
        assert entry.site_id == "site-b"
