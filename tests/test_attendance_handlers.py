"""Attendance handler flows (ticket-016): mocked Telegram, real engine."""

import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.ext import ContextTypes

from app.bot.handlers import attendance as att_handlers
from app.database.manager import DatabaseManager
from app.models.attendance import AttendanceStatus
from app.repositories.attendance_repository import AttendanceRepository
from app.services.attendance_service import AttendanceService

SITE = "default"  # driver.site_id() default; resolve mocked to this site
FENCE = {"type": "circle", "lat": 30.0, "lon": 31.0, "radius_m": 100}


def make_update(user_id=111, text=""):
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = f"User{user_id}"
    update.effective_user.last_name = ""
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.reply_text = AsyncMock()
    msg.photo = []
    msg.location = None
    update.message = msg
    update.callback_query = None
    update.effective_message = msg
    return update


def make_location_update(user_id, lat=30.0, lon=31.0):
    update = make_update(user_id)
    update.message.location = SimpleNamespace(
        latitude=lat, longitude=lon, horizontal_accuracy=10)
    return update


def make_callback(user_id, data):
    update = make_update(user_id)
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def make_auth(role="normal_user"):
    from app.auth import capabilities as caps
    from app.services.authorization_service import AuthorizationService

    auth = MagicMock(spec=AuthorizationService)
    auth.get_role.return_value = role
    auth.has_capability.side_effect = (
        lambda chat_id, cap, site_id=None: cap in caps.for_role(role))
    auth.resolve_active_site.return_value = SITE
    auth.sites_for_user.return_value = [SITE]
    return auth


def make_context(auth, service, sites=None, user_data=None, flow_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {
        "authorization_service": auth,
        "attendance_service": service,
        "app_config": SimpleNamespace(
            sites=[{"id": SITE, **(sites or {})}]),
    }
    context.user_data = user_data if user_data is not None else (
        flow_data if flow_data is not None else {})
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        manager = DatabaseManager(str(Path(tmp) / "a.db"))
        yield AttendanceService(AttendanceRepository(manager))
        manager.close_all()


@pytest.fixture
def flow_data():
    return {}


class TestGates:
    async def test_checkin_sets_state(self, service, flow_data):
        ctx = make_context(make_auth(), service, flow_data=flow_data)
        await att_handlers.checkin_command(make_update(111, "/checkin"), ctx)
        assert flow_data["state"] == "awaiting_location_in"

    async def test_viewer_denied(self, service, flow_data):
        ctx = make_context(make_auth("viewer"), service, flow_data=flow_data)
        await att_handlers.checkin_command(make_update(111, "/checkin"), ctx)
        assert "state" not in flow_data

    async def test_checkout_denied_for_viewer(self, service, flow_data):
        ctx = make_context(make_auth("viewer"), service, flow_data=flow_data)
        await att_handlers.checkout_command(make_update(111, "/checkout"), ctx)
        assert "state" not in flow_data


class TestLocationFlow:
    async def test_checkin_auto_confirms_without_confirmer(
            self, service, flow_data):
        flow_data["state"] = "awaiting_location_in"
        ctx = make_context(make_auth(), service, flow_data=flow_data)
        await att_handlers.handle_location(
            make_location_update(111), ctx)
        assert flow_data.get("state") is None
        rows = service._repo.for_user_day(
            "111", date.today().isoformat(), site_id=SITE)
        assert len(rows) == 1
        assert rows[0].status == AttendanceStatus.CONFIRMED
        assert rows[0].confirmed_by == "system"
        ctx.bot.send_message.assert_not_called()

    async def test_checkin_notifies_confirmer(self, service, flow_data):
        flow_data["state"] = "awaiting_location_in"
        ctx = make_context(make_auth(), service,
                           sites={"confirmer": "999"}, user_data=flow_data)
        await att_handlers.handle_location(
            make_location_update(111), ctx)
        rows = service._repo.for_user_day(
            "111", date.today().isoformat(), site_id=SITE)
        assert rows[0].status == AttendanceStatus.SUBMITTED
        ctx.bot.send_message.assert_called_once()
        assert ctx.bot.send_message.call_args.kwargs["chat_id"] == 999

    async def test_primary_self_routes_to_fallback(self, service, flow_data):
        flow_data["state"] = "awaiting_location_in"
        ctx = make_context(make_auth(), service, flow_data=flow_data,
                           sites={"confirmer": "111",
                                  "confirmer_fallback": "999"})
        await att_handlers.handle_location(
            make_location_update(111), ctx)
        ctx.bot.send_message.assert_called_once()
        assert ctx.bot.send_message.call_args.kwargs["chat_id"] == 999

    async def test_inside_verdict_with_fence(self, service, flow_data):
        flow_data["state"] = "awaiting_location_in"
        ctx = make_context(make_auth(), service, flow_data=flow_data,
                           sites={"geofence": FENCE})
        await att_handlers.handle_location(
            make_location_update(111), ctx)
        rows = service._repo.for_user_day(
            "111", date.today().isoformat(), site_id=SITE)
        assert rows[0].location_verdict == "inside"


class TestAssisted:
    async def test_usage_error(self, service, flow_data):
        ctx = make_context(make_auth("project_manager"), service,
                           user_data=flow_data)
        update = make_update(222, "/assisted")
        await att_handlers.assisted_command(update, ctx)
        update.effective_message.reply_text.assert_called_once_with(
            "Usage: /assisted <target_chat_id> <reason>")

    async def test_assisted_records_pending(self, service, flow_data):
        ctx = make_context(make_auth("project_manager"), service,
                           sites={"confirmer": "999"}, user_data=flow_data)
        update = make_update(222, "/assisted 111 broken phone")
        await att_handlers.assisted_command(update, ctx)
        rows = service._repo.for_user_day(
            "111", date.today().isoformat(), site_id=SITE)
        assert len(rows) == 1
        assert rows[0].method == "assisted"
        assert rows[0].status == AttendanceStatus.PENDING_VERIFICATION


class TestCallbacks:
    def _submitted_id(self, service):
        return service.check_in(
            "111", date.today().isoformat(), site_id=SITE).id

    async def test_confirm_by_pm(self, service, flow_data):
        case_id = self._submitted_id(service)
        ctx = make_context(make_auth("project_manager"), service,
                           user_data=flow_data)
        await att_handlers.handle_attendance_callback(
            make_callback(222, f"att_confirm:{case_id}"), ctx)
        assert service._repo.get_by_id(case_id).status == AttendanceStatus.CONFIRMED
        ctx.bot.send_message.assert_called_once()  # employee notified

    async def test_confirm_denied_for_normal(self, service, flow_data):
        case_id = self._submitted_id(service)
        ctx = make_context(make_auth("normal_user"), service, user_data=flow_data)
        update = make_callback(111, f"att_confirm:{case_id}")
        await att_handlers.handle_attendance_callback(update, ctx)
        update.callback_query.edit_message_text.assert_called_once_with(
            "Confirmation needs an attendance manager.")
        assert service._repo.get_by_id(case_id).status == AttendanceStatus.SUBMITTED

    async def test_dispute_sets_note_state(self, service, flow_data):
        case_id = self._submitted_id(service)
        ctx = make_context(make_auth("project_manager"), service,
                           user_data=flow_data)
        await att_handlers.handle_attendance_callback(
            make_callback(222, f"att_dispute:{case_id}"), ctx)
        assert flow_data["state"] == "awaiting_att_note"
        await att_handlers.handle_att_note(make_update(222, "wrong person"), ctx)
        assert service._repo.get_by_id(case_id).status == AttendanceStatus.DISPUTED
        ctx.bot.send_message.assert_called_once()

    async def test_site_pick(self, service, flow_data):
        auth = make_auth()
        ctx = make_context(auth, service, user_data=flow_data)
        update = make_callback(111, f"att_site:{SITE}")
        await att_handlers.handle_attendance_callback(update, ctx)
        assert flow_data["active_site"] == SITE

    async def test_site_pick_nonmember(self, service, flow_data):
        auth = make_auth()
        auth.sites_for_user.return_value = ["other"]
        ctx = make_context(auth, service, user_data=flow_data)
        update = make_callback(111, "att_site:site-a")
        await att_handlers.handle_attendance_callback(update, ctx)
        update.callback_query.edit_message_text.assert_called_once_with(
            "Not a member of that site.")
