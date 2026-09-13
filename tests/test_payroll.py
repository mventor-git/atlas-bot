"""Payroll engine tests (ticket-020): math, runs, import, lock, isolation."""

import tempfile
from pathlib import Path

import pytest

from app.database.manager import DatabaseManager
from app.models.database import User
from app.models.payroll import PayrollRunStatus
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.user_repository import UserRepository
from app.services.payroll_service import PayrollService
from app.utils.exceptions import DatabaseError


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "p.db"))
        users = UserRepository(manager)
        users.upsert(User(chat_id="u1", role="normal_user", site_id="site-a"))
        users.upsert(User(chat_id="u2", role="normal_user", site_id="site-a"))
        users.set_salary("u1", 12000.0, effective_from="2026-01-01")
        yield PayrollService(PayrollRepository(manager), users)
        manager.close_all()


class TestMath:
    def test_net_exact(self, service: PayrollService):
        ot, net = service.compute_net(12000.0, 10.0, 240.0, 1.5, 2000.0, 500.0)
        assert ot == 750.0  # 10 * (12000/240) * 1.5
        assert net == 10250.0

    def test_zero_inputs(self, service: PayrollService):
        ot, net = service.compute_net(8000.0, 0.0, 240.0, 1.5, 0.0, 0.0)
        assert (ot, net) == (0.0, 8000.0)

    def test_negatives_rejected(self, service: PayrollService):
        with pytest.raises(DatabaseError):
            service.compute_net(-1.0, 0.0, 240.0, 1.5, 0.0, 0.0)
        with pytest.raises(DatabaseError):
            service.compute_net(8000.0, 0.0, 240.0, 1.5, -5.0, 0.0)


class TestRuns:
    def test_create_and_line(self, service: PayrollService):
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        assert run.status == PayrollRunStatus.DRAFT
        line = service.add_line(run.id, "u1", 12000.0, 10.0, 2000.0, 500.0,
                                site_id="site-a")
        assert line.net == 10250.0
        assert len(service._repo.lines_for(run.id)) == 1

    def test_bad_period_rejected(self, service: PayrollService):
        with pytest.raises(DatabaseError):
            service.create_run("sep-2026", "hq1", site_id="site-a")

    def test_duplicate_period_refused(self, service: PayrollService):
        service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.create_run("2026-09", "hq1", site_id="site-a")

    def test_build_run_uses_salary_store(self, service: PayrollService):
        run = service.build_run("2026-09", "hq1",
                                [{"chat_id": "u1", "ot_hours": 10.0,
                                  "advances": 2000.0, "deductions": 500.0}],
                                site_id="site-a")
        (line,) = service._repo.lines_for(run.id)
        assert line.base_pay == 12000.0 and line.net == 10250.0

    def test_build_run_missing_salary_blocks(self, service: PayrollService):
        with pytest.raises(DatabaseError):
            service.build_run("2026-09", "hq1", [{"chat_id": "u2"}],
                              site_id="site-a")

    def test_update_line_recomputes(self, service: PayrollService):
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        line = service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        assert line.net == 12000.0
        fixed = service.update_line(line.id, run.id, 12000.0, 0.0, 1000.0, 0.0,
                                    site_id="site-a")
        assert fixed.net == 11000.0


class TestLock:
    def _exported(self, service):
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        return service.mark_exported(run.id, site_id="site-a")

    def test_export_locks(self, service: PayrollService):
        run = self._exported(service)
        assert run.status == PayrollRunStatus.EXPORTED
        assert run.exported_at is not None

    def test_locked_run_immutable(self, service: PayrollService):
        run = self._exported(service)
        with pytest.raises(DatabaseError):
            service.add_line(run.id, "u1", 12000.0, site_id="site-a")
        line = service._repo.lines_for(run.id)[0]
        with pytest.raises(DatabaseError):
            service.update_line(line.id, run.id, 12000.0, 0.0, 0.0, 0.0,
                                site_id="site-a")
        with pytest.raises(DatabaseError):
            service.mark_exported(run.id, site_id="site-a")


class TestImport:
    def test_csv_import(self, service: PayrollService):
        report = service.import_salaries("chat_id,salary\nu1,13000\nu9,5000\nbad-line\n")
        assert report == {"updated": 1, "unknown": 1, "skipped": 2}
        assert service._users.get_by_chat_id("u1").monthly_salary == 13000.0

    def test_negative_salary_skipped(self, service: PayrollService):
        report = service.import_salaries("u1,-5")
        assert report["skipped"] == 1
        assert service._users.get_by_chat_id("u1").monthly_salary == 12000.0


class TestIsolation:
    def test_cross_site_denied(self, service: PayrollService):
        run = service.create_run("2026-09", "hq1", site_id="site-a")
        with pytest.raises(DatabaseError):
            service.add_line(run.id, "u1", 12000.0, site_id="site-b")

    def test_periods_isolated_per_site(self, service: PayrollService):
        service.create_run("2026-09", "hq1", site_id="site-a")
        other = service.create_run("2026-09", "hq1", site_id="site-b")
        assert other.site_id == "site-b"
