"""Tenancy tests (ticket-008): registry, memberships, migration, resolver."""

import tempfile
from pathlib import Path

import pytest

from app.auth import capabilities as caps
from app.database.manager import DatabaseManager
from app.models.database import User
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository
from app.services.authorization_service import AuthorizationService


@pytest.fixture
def dbs():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "t.db"))
        yield manager
        manager.close_all()


@pytest.fixture
def auth(dbs):
    users = UserRepository(dbs)
    memberships = MembershipRepository(dbs)
    service = AuthorizationService(users, super_admin_chat_id="1",
                                   membership_repo=memberships)
    return service, users, memberships


def _user(users, chat, role="normal_user", site="site-a"):
    return users.upsert(User(chat_id=chat, role=role, site_id=site))


class TestRegistry:
    def test_known_capabilities(self):
        assert caps.is_known("confirm_hr_request")
        assert caps.is_known("decide_hr_request")
        assert caps.is_known("submit_hr_request")
        assert not caps.is_known("fly_to_moon")

    def test_role_defaults(self):
        assert "decide_hr_request" in caps.for_role("hr")
        assert "decide_hr_request" not in caps.for_role("normal_user")
        assert "confirm_hr_request" in caps.for_role("project_manager")
        assert caps.for_role("pending") == frozenset()


class TestMemberships:
    def test_grant_find_suspend(self, dbs):
        repo = MembershipRepository(dbs)
        membership = repo.grant("u1", "site-a", ["view_site_reports"])
        assert membership.id is not None
        assert repo.find("u1", "site-a").status == "active"
        assert repo.suspend("u1", "site-a") is True
        assert repo.find("u1", "site-a").status == "suspended"
        assert repo.active_for_user("u1") == []

    def test_migration_from_users_site(self, dbs):
        users = UserRepository(dbs)
        memberships = MembershipRepository(dbs)
        users.upsert(User(chat_id="u1", role="normal_user", site_id="site-a"))
        users.upsert(User(chat_id="u2", role="normal_user", site_id="site-b"))
        created = memberships.migrate_from_users(users)
        assert created == 2
        assert memberships.find("u1", "site-a") is not None
        assert memberships.find("u2", "site-b") is not None
        # idempotent
        assert memberships.migrate_from_users(users) == 0


class TestHasCapability:
    def test_role_default_without_membership_rows(self, auth, monkeypatch):
        service, users, _ = auth
        _user(users, "u1", "hr", "site-a")
        monkeypatch.setenv("SITE_ID", "site-a")
        # No membership rows: legacy path denies (migration creates them)
        assert service.has_capability("u1", "decide_hr_request") is False

    def test_migrated_user_gets_role_defaults(self, auth, monkeypatch):
        service, users, memberships = auth
        _user(users, "u1", "hr", "site-a")
        memberships.migrate_from_users(users)
        monkeypatch.setenv("SITE_ID", "site-a")
        assert service.has_capability("u1", "decide_hr_request") is True
        assert service.has_capability("u1", "approve_daily_report") is False

    def test_explicit_grant_wins(self, auth, monkeypatch):
        service, users, memberships = auth
        _user(users, "u1", "normal_user", "site-a")
        memberships.migrate_from_users(users)
        memberships.grant("u1", "site-a", ["decide_hr_request"])
        monkeypatch.setenv("SITE_ID", "site-a")
        assert service.has_capability("u1", "decide_hr_request") is True

    def test_suspended_denies(self, auth, monkeypatch):
        service, users, memberships = auth
        _user(users, "u1", "hr", "site-a")
        memberships.migrate_from_users(users)
        memberships.suspend("u1", "site-a")
        monkeypatch.setenv("SITE_ID", "site-a")
        assert service.has_capability("u1", "decide_hr_request") is False

    def test_cross_site_denied(self, auth, monkeypatch):
        service, users, memberships = auth
        _user(users, "u1", "hr", "site-a")
        memberships.migrate_from_users(users)
        monkeypatch.setenv("SITE_ID", "site-b")
        assert service.has_capability("u1", "decide_hr_request") is False

    def test_unknown_capability_denies(self, auth, monkeypatch):
        service, users, memberships = auth
        _user(users, "1", "superadmin", "site-a")
        memberships.migrate_from_users(users)
        monkeypatch.setenv("SITE_ID", "site-a")
        assert service.has_capability("1", "fly_to_moon") is False

    def test_pending_denies(self, auth, monkeypatch):
        service, users, _ = auth
        _user(users, "u9", "pending", "site-a")
        monkeypatch.setenv("SITE_ID", "site-a")
        assert service.has_capability("u9", "submit_hr_request") is False


class TestResolver:
    def test_auto_binds_single(self, auth):
        service, users, memberships = auth
        _user(users, "u1", "normal_user", "site-a")
        memberships.migrate_from_users(users)
        assert service.resolve_active_site("u1", None) == "site-a"
        assert service.resolve_active_site("u1", "site-a") == "site-a"

    def test_multi_needs_pick(self, auth):
        service, users, memberships = auth
        _user(users, "u1", "normal_user", "site-a")
        memberships.migrate_from_users(users)
        memberships.grant("u1", "site-b", [])
        assert service.resolve_active_site("u1", None) is None
        assert service.resolve_active_site("u1", "site-b") == "site-b"
        assert service.resolve_active_site("u1", "site-c") is None

    def test_no_membership_none(self, auth):
        service, _, _ = auth
        assert service.resolve_active_site("ghost", "site-a") is None
