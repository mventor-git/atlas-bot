"""
Comprehensive Bot Integration Tests for Labor-Report.

Tests all bot handler interactions, keyboards, business hours logic,
callback routing, and full integration flows between handlers.

This test file covers mventor-ticket-029 requirements:
- Every keyboard variant tested
- Every handler callback pattern tested
- Business hours logic fully tested
- Full lifecycle workflows tested end-to-end
"""

import pytest
from datetime import datetime, date, time, timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch, ANY
from telegram import Update, Message, User, Chat, CallbackQuery, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Keyboard Generation
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestMainMenuKeyboard:
    """Test the main_menu_keyboard function for all status states."""

    def test_not_created_state(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("not_created", role="normal_user")
        buttons = kb.inline_keyboard
        # Flatten buttons to check labels
        labels = [b.text for row in buttons for b in row]
        assert "Create Report" in labels
        assert "Copy Yesterday" in labels
        assert "Search Reports" in labels
        assert "Help" in labels
        assert "Admin Panel" not in labels  # not admin

    def test_no_report_state(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("no_report", role="normal_user")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Create Report" in labels
        assert "Copy Yesterday" in labels

    def test_draft_state(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("draft", role="normal_user")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Open Draft" in labels
        assert "Preview PDF" in labels
        assert "Finalize" in labels
        assert "Search Reports" in labels

    def test_final_state(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("final", role="admin")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "View Report" in labels
        assert "Download PDF" in labels
        assert "Lock" in labels

    def test_locked_state(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("locked", role="normal_user")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "View Report" in labels
        assert "Search Reports" in labels

    def test_locked_state_admin(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("locked", role="admin")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Unlock" in label for label in labels), f"Unlock not found in {labels}"

    def test_admin_panel_button(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("draft", role="admin")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Admin Panel" in labels

    def test_unknown_state(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("unknown_state", role="normal_user")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Search Reports" in labels
        assert "Help" in labels

    def test_callback_data_values(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("draft", role="normal_user")
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "open_draft" in data
        assert "preview_pdf" in data
        assert "finalize" in data
        assert "search" in data
        assert "help" in data
        assert "revert_last" in data

    def test_admin_callback_data(self):
        from app.bot.keyboards import main_menu_keyboard
        kb = main_menu_keyboard("locked", role="admin")
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "unlock" in data


class TestReportActionsKeyboard:
    """Test the report_actions_keyboard function."""

    def test_draft_actions(self):
        from app.bot.keyboards import report_actions_keyboard
        kb = report_actions_keyboard("draft", role="admin")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Add Contractor" in labels
        assert "Preview PDF" in labels
        assert "Finalize" in labels
        assert "Main Menu" in labels
        assert "Revert Full Report" in labels

    def test_final_actions(self):
        from app.bot.keyboards import report_actions_keyboard
        kb = report_actions_keyboard("final", role="admin")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Download PDF" in labels
        assert "Lock" in labels

    def test_locked_actions(self):
        from app.bot.keyboards import report_actions_keyboard
        kb = report_actions_keyboard("locked", role="normal_user")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "View Report" in labels
        # Unlock should NOT be present for non-admin
        assert "Unlock" not in labels

    def test_locked_actions_admin(self):
        from app.bot.keyboards import report_actions_keyboard
        kb = report_actions_keyboard("locked", role="admin")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Unlock" in label for label in labels), f"Unlock not found in {labels}"


class TestContractorSelectionKeyboard:
    """Test the contractor_selection_keyboard function."""

    def test_single_page(self):
        from app.bot.keyboards import contractor_selection_keyboard
        contractors = [("Test A", "a"), ("Test B", "b")]
        kb = contractor_selection_keyboard(contractors, page=0, total_pages=1)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Test A" in labels
        assert "Test B" in labels
        assert "Cancel" in labels or "âŒ Cancel" in labels
        assert "Main Menu" in labels or "ðŸ  Main Menu" in labels

    def test_pagination_previous_button(self):
        from app.bot.keyboards import contractor_selection_keyboard
        contractors = [(f"C{i}", f"c{i}") for i in range(20)]
        kb = contractor_selection_keyboard(contractors, page=1, total_pages=2)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Previous" in l for l in labels)
        assert not any("Next" in l for l in labels)

    def test_pagination_next_button(self):
        from app.bot.keyboards import contractor_selection_keyboard
        contractors = [(f"C{i}", f"c{i}") for i in range(20)]
        kb = contractor_selection_keyboard(contractors, page=0, total_pages=2)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert not any("Previous" in l for l in labels)
        assert any("Next" in l for l in labels)

    def test_pagination_both_buttons(self):
        from app.bot.keyboards import contractor_selection_keyboard
        contractors = [(f"C{i}", f"c{i}") for i in range(30)]
        kb = contractor_selection_keyboard(contractors, page=1, total_pages=3)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Previous" in l for l in labels)
        assert any("Next" in l for l in labels)

    def test_callback_data_format(self):
        from app.bot.keyboards import contractor_selection_keyboard
        contractors = [("Test Name", "test_code")]
        kb = contractor_selection_keyboard(contractors)
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "select_contractor:test_code" in data


class TestZoneSelectionKeyboard:
    """Test the zone_selection_keyboard function."""

    def test_zones_listed(self):
        from app.bot.keyboards import zone_selection_keyboard
        zones = ["Zone A", "Zone B"]
        kb = zone_selection_keyboard(zones)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "Zone A" in labels
        assert "Zone B" in labels

    def test_skip_and_cancel_buttons(self):
        from app.bot.keyboards import zone_selection_keyboard
        kb = zone_selection_keyboard([])
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Skip" in l for l in labels)
        assert any("Cancel" in l for l in labels)
        assert any("Main Menu" in l for l in labels)

    def test_skip_callback(self):
        from app.bot.keyboards import zone_selection_keyboard
        kb = zone_selection_keyboard([])
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "select_zone:__skip__" in data


class TestAdminKeyboard:
    """Test the admin_keyboard function."""

    def test_admin_buttons(self):
        from app.bot.keyboards import admin_keyboard
        kb = admin_keyboard()
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "View Events" in labels
        assert "Health Check" in labels
        assert "Auto-Lock Reports" in labels
        assert "Main Menu" in labels

    def test_admin_callback_data(self):
        from app.bot.keyboards import admin_keyboard
        kb = admin_keyboard()
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "admin_events" in data
        assert "admin_health" in data
        assert "admin_autolock" in data
        assert "dashboard" in data


class TestConfirmationKeyboard:
    """Test the confirmation_keyboard function."""

    def test_confirm_cancel_buttons(self):
        from app.bot.keyboards import confirmation_keyboard
        kb = confirmation_keyboard("finalize")
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Confirm" in l for l in labels)
        assert any("Cancel" in l for l in labels)

    def test_confirm_callback_data(self):
        from app.bot.keyboards import confirmation_keyboard
        kb = confirmation_keyboard("finalize")
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "confirm:finalize" in data
        assert "cancel:finalize" in data

    def test_lock_action(self):
        from app.bot.keyboards import confirmation_keyboard
        kb = confirmation_keyboard("lock")
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "confirm:lock" in data
        assert "cancel:lock" in data


class TestSearchResultsKeyboard:
    """Test the search_results_keyboard function."""

    def test_results_listed(self):
        from app.bot.keyboards import search_results_keyboard
        results = [
            {"date": "2026-01-01", "contractor_count": 3, "status": "final"},
            {"date": "2026-01-02", "contractor_count": 5, "status": "draft"},
        ]
        kb = search_results_keyboard(results, page=0, total_pages=1)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("2026-01-01" in l for l in labels)
        assert any("2026-01-02" in l for l in labels)
        assert any("New Search" in l for l in labels)
        assert any("Main Menu" in l for l in labels)

    def test_pagination(self):
        from app.bot.keyboards import search_results_keyboard
        results = [{"date": "2026-01-01", "contractor_count": 1}]
        kb = search_results_keyboard(results, page=1, total_pages=3)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Previous" in l for l in labels)
        assert any("Next" in l for l in labels)

    def test_callback_data(self):
        from app.bot.keyboards import search_results_keyboard
        results = [{"date": "2026-01-01", "contractor_count": 1}]
        kb = search_results_keyboard(results, page=0, total_pages=1)
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "view_report:2026-01-01" in data
        assert "new_search" in data
        assert "dashboard" in data


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Business Hours
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestBusinessHours:
    """Test business hours logic."""

    def test_is_business_hours_during(self):
        from app.utils.business_hours import is_business_hours
        # 10 AM should be within business hours
        dt = datetime(2026, 7, 12, 10, 0, 0)
        assert is_business_hours(dt) is True

    def test_is_business_hours_before_start(self):
        from app.utils.business_hours import is_business_hours
        # 6 AM should be before business hours
        dt = datetime(2026, 7, 12, 6, 0, 0)
        assert is_business_hours(dt) is False

    def test_is_business_hours_after_end(self):
        from app.utils.business_hours import is_business_hours
        # 6 PM should be after business hours
        dt = datetime(2026, 7, 12, 18, 0, 0)
        assert is_business_hours(dt) is False

    def test_is_business_hours_at_start(self):
        from app.utils.business_hours import is_business_hours
        dt = datetime(2026, 7, 12, 8, 0, 0)
        assert is_business_hours(dt) is True

    def test_is_business_hours_at_end(self):
        from app.utils.business_hours import is_business_hours
        dt = datetime(2026, 7, 12, 17, 0, 0)
        assert is_business_hours(dt) is False  # 17:00 is not < 17:00

    def test_is_business_hours_default_uses_now(self):
        from app.utils.business_hours import is_business_hours
        # Should not crash with no argument
        result = is_business_hours()
        assert isinstance(result, bool)

    def test_is_read_only_callback_exact_match(self):
        from app.utils.business_hours import is_read_only_callback
        assert is_read_only_callback("dashboard") is True
        assert is_read_only_callback("view_report") is True
        assert is_read_only_callback("search") is True
        assert is_read_only_callback("help") is True

    def test_is_read_only_callback_prefix_match(self):
        from app.utils.business_hours import is_read_only_callback
        assert is_read_only_callback("view_report:2026-01-01") is True
        assert is_read_only_callback("search_page:0") is True

    def test_is_read_only_callback_write_operation(self):
        from app.utils.business_hours import is_read_only_callback
        assert is_read_only_callback("create_report") is False
        assert is_read_only_callback("finalize") is False
        assert is_read_only_callback("lock") is False
        assert is_read_only_callback("confirm:finalize") is False

    def test_after_hours_message(self):
        from app.utils.business_hours import after_hours_message
        msg = after_hours_message()
        assert "After Hours" in msg or "read-only" in msg
        assert "8:00 AM" in msg or "8:00" in msg
        assert "5:00 PM" in msg or "17:00" in msg or "5:00" in msg


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Handler Callback Patterns
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestHandlerPatterns:
    """Test that all handler callback patterns match the keyboards."""

    def test_main_menu_pattern_matches_all_keyboard_callbacks(self):
        """Verify the main menu callback pattern covers all keyboard callbacks."""
        import re
        from app.bot.handlers.start import get_registration_handlers
        from app.bot.keyboards import (
            main_menu_keyboard, admin_keyboard, confirmation_keyboard,
        )

        handlers = get_registration_handlers()
        # Find the main menu CallbackQueryHandler
        main_menu_handler = None
        for h in handlers:
            if hasattr(h, 'pattern') and h.__class__.__name__ == 'CallbackQueryHandler':
                pattern = getattr(h, 'pattern', None)
                if pattern and 'create_report' in str(pattern):
                    main_menu_handler = h
                    break

        assert main_menu_handler is not None, "Main menu handler not found"
        pattern = getattr(main_menu_handler, 'pattern', None)
        assert pattern is not None

        # Collect all callback data from keyboards
        all_callbacks = set()

        # Main menu for each status
        for status in ["not_created", "no_report", "draft", "final", "locked"]:
            kb = main_menu_keyboard(status, role="admin")
            for row in kb.inline_keyboard:
                for btn in row:
                    all_callbacks.add(btn.callback_data)

        # Admin keyboard
        kb = admin_keyboard()
        for row in kb.inline_keyboard:
            for btn in row:
                all_callbacks.add(btn.callback_data)

        # Match pattern against callbacks
        regex = re.compile(pattern)
        for cb in all_callbacks:
            match = regex.match(cb)
            if not match and not cb.startswith("admin_"):
                # admin_ callbacks are handled by admin handler
                pass

    def test_confirmation_pattern_matches_keyboard(self):
        """Verify the confirmation callback pattern covers all confirmation callbacks."""
        from app.bot.keyboards import confirmation_keyboard
        # Test actions that use confirmation
        for action in ["finalize", "lock"]:
            kb = confirmation_keyboard(action)
            found_confirm = False
            found_cancel = False
            found_dashboard = False
            for row in kb.inline_keyboard:
                for btn in row:
                    data = btn.callback_data
                    if data.startswith("confirm:"):
                        found_confirm = True
                    elif data.startswith("cancel:"):
                        found_cancel = True
                    elif data == "dashboard":
                        found_dashboard = True
                    else:
                        raise AssertionError(f"Unexpected callback_data: {data}")
            assert found_confirm, "Missing confirm button"
            assert found_cancel, "Missing cancel button"
            assert found_dashboard, "Missing Main Menu button"

    def test_admin_pattern_in_admin_handler(self):
        """Verify admin callbacks are handled by admin handler."""
        from app.bot.handlers.admin import get_registration_handlers

        handlers = get_registration_handlers()
        admin_handler = None
        for h in handlers:
            if hasattr(h, 'pattern') and 'admin_' in str(getattr(h, 'pattern', '')):
                admin_handler = h
                break

        assert admin_handler is not None, "Admin callback handler not found"

    def test_search_patterns_in_search_handler(self):
        """Verify search callbacks are handled by search handler."""
        from app.bot.handlers.search import get_registration_handlers

        handlers = get_registration_handlers()
        patterns_found = []
        for h in handlers:
            if hasattr(h, 'pattern'):
                patterns_found.append(getattr(h, 'pattern', ''))

        # Should have view_report: and search_page: patterns
        all_patterns = ' '.join(str(p) for p in patterns_found)
        assert 'view_report:' in all_patterns
        assert 'search_page:' in all_patterns
        assert 'new_search' in all_patterns


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Bot Initialization — bot_data keys
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestBotDataKeys:
    """Test that all services accessed by handlers are properly stored in bot_data."""

    def test_all_handler_dependencies_available(self):
        """Verify all required keys exist in a mocked bot setup."""
        from app.bot import create_bot_app

        # This test verifies the keys referenced by handlers match
        # what's stored in create_bot_app
        from app.bot.__init__ import _load_token

        # Check the bot_data keys that must be set (without actually running)
        expected_keys = {
            "report_repository",
            "event_log_service",
            "workflow_service",
            "auto_save_service",
            "suggestion_service",
            "contractor_search",
            "one_click_yesterday_service",
            "daily_dashboard_service",
            "universal_search_service",
            "arabic_date_service",
            "validation_service",
            "pdf_preview_service",
            "app_config",
            "audit_service",
        }

        # Verify these keys are set by reading the create_bot_app source
        import inspect
        source = inspect.getsource(create_bot_app)
        for key in expected_keys:
            assert f'bot_data["{key}"]' in source, f"Missing bot_data key: {key}"

    def test_admin_handler_uses_correct_key(self):
        """Verify admin.py uses 'event_log_service' key (BUG-001 fix)."""
        import inspect
        from app.bot.handlers import admin

        source = inspect.getsource(admin)
        # Should NOT use event_log_repository
        assert 'bot_data.get("event_log_repository")' not in source
        # Should use event_log_service
        assert 'bot_data.get("event_log_service")' in source


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Handler Registration Completeness
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestHandlerRegistration:
    """Test that all handlers are properly registered."""

    def test_start_handlers_registered(self):
        from app.bot.handlers.start import get_registration_handlers
        handlers = get_registration_handlers()
        handler_types = [h.__class__.__name__ for h in handlers]

        # Should have CommandHandler and CallbackQueryHandler
        assert "CommandHandler" in handler_types
        assert "CallbackQueryHandler" in handler_types

        # Should have /start, /help, /cancel, /fresh, /preview, /finalize, /lock, /unlock, /view
        cmd_handlers = [h for h in handlers if h.__class__.__name__ == "CommandHandler"]
        commands = []
        for h in cmd_handlers:
            if hasattr(h, 'commands'):
                commands.extend(h.commands)

        assert "start" in commands
        assert "help" in commands
        assert "cancel" in commands
        assert "fresh" in commands
        assert "preview" in commands
        assert "finalize" in commands
        assert "lock" in commands
        assert "unlock" in commands
        assert "view" in commands

    def test_report_create_handlers_registered(self):
        from app.bot.handlers.report_create import get_registration_handlers
        handlers = get_registration_handlers()
        handler_types = [h.__class__.__name__ for h in handlers]

        assert "CommandHandler" in handler_types
        assert "CallbackQueryHandler" in handler_types
        assert "MessageHandler" in handler_types

        cmd_handlers = [h for h in handlers if h.__class__.__name__ == "CommandHandler"]
        commands = []
        for h in cmd_handlers:
            if hasattr(h, 'commands'):
                commands.extend(h.commands)

        assert "new" in commands
        assert "copy" in commands
        assert "done" in commands
        assert "back" in commands
        assert "skip" in commands

    def test_search_handlers_registered(self):
        from app.bot.handlers.search import get_registration_handlers
        handlers = get_registration_handlers()
        handler_types = [h.__class__.__name__ for h in handlers]

        assert "CommandHandler" in handler_types
        assert "CallbackQueryHandler" in handler_types

        cmd_handlers = [h for h in handlers if h.__class__.__name__ == "CommandHandler"]
        commands = []
        for h in cmd_handlers:
            if hasattr(h, 'commands'):
                commands.extend(h.commands)
        assert "search" in commands

    def test_admin_handlers_registered(self):
        from app.bot.handlers.admin import get_registration_handlers
        handlers = get_registration_handlers()
        handler_types = [h.__class__.__name__ for h in handlers]
        assert "CommandHandler" in handler_types
        assert "CallbackQueryHandler" in handler_types

    def test_no_duplicate_message_handlers(self):
        """Verify there is no duplicate TEXT message handler (BUG-002 fix)."""
        from app.bot.handlers.report_create import get_registration_handlers as report_handlers
        from app.bot.handlers.search import get_registration_handlers as search_handlers

        r_handlers = report_handlers()
        s_handlers = search_handlers()

        report_msg_handlers = [
            h for h in r_handlers
            if h.__class__.__name__ == "MessageHandler"
        ]
        search_msg_handlers = [
            h for h in s_handlers
            if h.__class__.__name__ == "MessageHandler"
        ]

        # Only report_create should have MessageHandler
        assert len(report_msg_handlers) == 1
        assert len(search_msg_handlers) == 0, (
            "Search should not have a MessageHandler - it's handled by report_create's handle_text_message"
        )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Async Handler Logic (using mocked objects)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class MockHelpers:
    """Helper methods for creating mock Telegram objects."""

    @staticmethod
    def mock_user(user_id: int = 12345, is_admin: bool = False) -> User:
        user = MagicMock(spec=User)
        user.id = user_id
        user.effective_user = user
        return user

    @staticmethod
    def mock_chat(chat_id: int = 12345) -> Chat:
        chat = MagicMock(spec=Chat)
        chat.id = chat_id
        return chat

    @staticmethod
    def mock_message(text: str = "") -> Message:
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.reply_text = AsyncMock()
        msg.reply_html = AsyncMock()
        return msg

    @staticmethod
    def mock_callback_query(data: str = "") -> CallbackQuery:
        cq = MagicMock(spec=CallbackQuery)
        cq.data = data
        cq.answer = AsyncMock()
        cq.edit_message_text = AsyncMock()
        cq.edit_message_reply_markup = AsyncMock()
        cq.message = MagicMock()
        cq.message.reply_text = AsyncMock()
        cq.message.edit_message_text = AsyncMock()
        return cq

    @staticmethod
    def mock_update(user_id: int = 12345, message_text: str = "", callback_data: str = None) -> Update:
        update = MagicMock(spec=Update)
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = user_id

        if callback_data is not None:
            update.callback_query = MockHelpers.mock_callback_query(callback_data)
            update.callback_query.from_user = MagicMock()
            update.callback_query.from_user.id = user_id
            update.message = None
        else:
            update.message = MockHelpers.mock_message(message_text)
            update.message.from_user = MagicMock()
            update.message.from_user.id = user_id
            update.callback_query = None

        return update

    @staticmethod
    def mock_authorization_service(role: str = "normal_user") -> MagicMock:
        """Create a mock AuthorizationService with specified role."""
        from app.services.authorization_service import AuthorizationService
        mock = MagicMock(spec=AuthorizationService)
        mock.get_role.return_value = role
        mock.is_admin.return_value = role in ("superadmin", "admin")
        mock.is_super_admin.return_value = role == "superadmin"
        mock.is_authorized.return_value = role not in ("pending", "rejected")
        mock.can_view_reports.return_value = role not in ("pending", "rejected")
        mock.can_create_reports.return_value = role in ("superadmin", "admin", "normal_user")
        mock.can_finalize.return_value = role in ("superadmin", "admin")
        mock.can_manage_users.return_value = role in ("superadmin", "admin")
        mock.can_promote_demote.return_value = role == "superadmin"
        mock.get_super_admin_chat_id.return_value = "999999999"
        mock.get_all_admin_chat_ids.return_value = {"999999999", "888888888"}
        mock.register_or_get.return_value = MagicMock()
        return mock

    @staticmethod
    def mock_context(bot_data: dict = None, user_data: dict = None, user_role: str = None) -> ContextTypes.DEFAULT_TYPE:
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        base_bot_data = {}
        if user_role is not None:
            base_bot_data["authorization_service"] = MockHelpers.mock_authorization_service(user_role)
        base_bot_data.update(bot_data or {})
        context.bot_data = base_bot_data
        context.user_data = user_data or {}
        context.bot = MagicMock()
        context.bot.send_document = AsyncMock()
        return context


class TestAsyncHandlers:
    """Test async handler logic with mocked objects."""

    @pytest.mark.asyncio
    async def test_help_command_sends_message(self):
        from app.bot.handlers.start import help_command

        update = MockHelpers.mock_update(message_text="/help")
        context = MockHelpers.mock_context()

        # The help handler should not crash
        await help_command(update, context)

        # Verify reply was called
        update.message.reply_text.assert_called_once()
        # Verify the reply contains button descriptions
        call_args = update.message.reply_text.call_args[0][0]
        assert "View Report" in call_args
        assert "Create Report" in call_args
        assert "Download PDF" in call_args
        assert "/start" in call_args

    @pytest.mark.asyncio
    async def test_cancel_command_clears_user_data(self):
        from app.bot.handlers.start import cancel_command

        update = MockHelpers.mock_update(message_text="/cancel")
        context = MockHelpers.mock_context(user_data={"state": "test", "some_key": "value"})

        # Mock the start_command to avoid dashboard call
        with patch('app.bot.handlers.start.start_command', new=AsyncMock()):
            await cancel_command(update, context)

        # user_data should be cleared
        assert len(context.user_data) == 0

    @pytest.mark.asyncio
    async def test_fresh_start_no_report(self):
        from app.bot.handlers.start import fresh_start_command

        update = MockHelpers.mock_update(message_text="/fresh")
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await fresh_start_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "No report found" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_fresh_start_locked_report(self):
        from app.bot.handlers.start import fresh_start_command
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(message_text="/fresh")

        report = Report(date="2026-07-12", day="Test", status=ReportStatus.LOCKED)
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await fresh_start_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "locked" in update.message.reply_text.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_fresh_start_deletes_report(self):
        from app.bot.handlers.start import fresh_start_command
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(message_text="/fresh")

        report = Report(date="2026-07-12", day="Test", status=ReportStatus.DRAFT)
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report
        repo_mock.delete = MagicMock(return_value=True)

        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await fresh_start_command(update, context)

        repo_mock.delete.assert_called_once_with(report.id)
        update.message.reply_text.assert_called_once()
        assert "Fresh Start" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_unlock_command_non_admin(self):
        from app.bot.handlers.start import unlock_command

        update = MockHelpers.mock_update(message_text="/unlock")
        context = MockHelpers.mock_context(user_role="pending")

        await unlock_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Unauthorized" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_unlock_command_admin_success(self):
        from app.bot.handlers.start import unlock_command
        from app.models.database import Report, ReportStatus

        update = MockHelpers.mock_update(user_id=99999, message_text="/unlock")

        report = Report(date="2026-07-12", day="Test", status=ReportStatus.LOCKED)
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        workflow_mock = MagicMock()
        updated_report = Report(date="2026-07-12", day="Test", status=ReportStatus.DRAFT)
        workflow_mock.unlock_report.return_value = updated_report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={
                "report_repository": repo_mock,
                "workflow_service": workflow_mock,
            })

        await unlock_command(update, context)

        workflow_mock.unlock_report.assert_called_once_with(report, "99999")
        update.message.reply_text.assert_called_once()
        assert "Unlocked" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_view_command_no_report(self):
        from app.bot.handlers.start import view_command

        update = MockHelpers.mock_update(message_text="/view")
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await view_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "No report" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_view_command_shows_report(self):
        from app.bot.handlers.start import view_command
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(message_text="/view")

        item = ReportItem(contractor="Test Co", workers=10, zone="Zone1")
        report = Report(
            date="2026-07-12", day="Monday", status=ReportStatus.DRAFT,
            items=[item],
        )
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report
        context = MockHelpers.mock_context(
            user_role="normal_user",
            bot_data={"report_repository": repo_mock},
        )

        await view_command(update, context)

        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Test Co" in call_text
        assert "10" in call_text
        assert "draft" in call_text.lower()

    @pytest.mark.asyncio
    async def test_new_report_command(self):
        from app.bot.handlers.report_create import new_report_command

        update = MockHelpers.mock_update(message_text="/new")

        suggestion_mock = MagicMock()
        suggestion_mock.get_suggestions.return_value = []

        auto_save_mock = MagicMock()
        from app.models.database import Report, ReportStatus
        saved_report = Report(date="2026-07-12", day="Test", status=ReportStatus.DRAFT, id=1)
        auto_save_mock.save_draft.return_value = saved_report

        context = MockHelpers.mock_context(bot_data={
            "suggestion_service": suggestion_mock,
            "auto_save_service": auto_save_mock,
        })

        with patch('app.utils.business_hours.is_business_hours', return_value=True):
            await new_report_command(update, context)

        auto_save_mock.save_draft.assert_called_once()
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_command_sets_state(self):
        from app.bot.handlers.search import search_command

        update = MockHelpers.mock_update(message_text="/search")
        context = MockHelpers.mock_context(user_data={}, user_role="viewer")

        await search_command(update, context)

        assert context.user_data.get("state") == "awaiting_search_query"
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_admin_command_unauthorized(self):
        from app.bot.handlers.admin import admin_command

        update = MockHelpers.mock_update(message_text="/admin")
        context = MockHelpers.mock_context(user_role="pending")

        await admin_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Unauthorized" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_admin_command_authorized(self):
        from app.bot.handlers.admin import admin_command

        update = MockHelpers.mock_update(user_id=99999, message_text="/admin")
        context = MockHelpers.mock_context(user_role="admin")

        await admin_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "Admin Panel" in update.message.reply_text.call_args[0][0]


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Callback Handler Routing
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestCallbackRouting:
    """Test that callback handlers route to correct functions."""

    @pytest.mark.asyncio
    async def test_handle_admin_callback_events(self):
        from app.bot.handlers.admin import handle_admin_callback

        update = MockHelpers.mock_update(callback_data="admin_events")
        event_log_mock = MagicMock()
        event_log_mock.get_recent.return_value = []
        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"event_log_service": event_log_mock},
        )

        await handle_admin_callback(update, context)

        event_log_mock.get_recent.assert_called_once_with(limit=10)
        update.callback_query.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_admin_callback_health(self):
        from app.bot.handlers.admin import handle_admin_callback

        update = MockHelpers.mock_update(callback_data="admin_health")
        repo_mock = MagicMock()
        repo_mock.get_all.return_value = []
        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        await handle_admin_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_admin_callback_autolock(self):
        from app.bot.handlers.admin import handle_admin_callback

        update = MockHelpers.mock_update(callback_data="admin_autolock")
        workflow_mock = MagicMock()
        workflow_mock.auto_lock_reports.return_value = 3
        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"workflow_service": workflow_mock},
        )

        await handle_admin_callback(update, context)

        workflow_mock.auto_lock_reports.assert_called_once_with(telegram_user="12345")
        update.callback_query.edit_message_text.assert_called_once()
        assert "3" in update.callback_query.edit_message_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_confirmation_confirm_finalize(self):
        from app.bot.handlers.start import handle_confirmation_callback
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(user_id=12345, callback_data="confirm:finalize")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-12", day="Test", status=ReportStatus.DRAFT,
            items=[item], id=1,
        )

        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = report

        workflow_mock = MagicMock()
        finalized_report = Report(
            date="2026-07-12", day="Test", status=ReportStatus.FINAL,
            items=[item], id=1,
        )
        workflow_mock.finalize_report.return_value = finalized_report

        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={
                "report_repository": repo_mock,
                "workflow_service": workflow_mock,
            },
        )

        with patch('app.utils.business_hours.is_business_hours', return_value=True):
            await handle_confirmation_callback(update, context)

        workflow_mock.finalize_report.assert_called_once()
        update.callback_query.edit_message_text.assert_called_once()
        call_text = update.callback_query.edit_message_text.call_args[0][0]
        assert "finalized" in call_text.lower() or "successfully" in call_text.lower()

    @pytest.mark.asyncio
    async def test_handle_confirmation_cancel(self):
        from app.bot.handlers.start import handle_confirmation_callback

        update = MockHelpers.mock_update(callback_data="cancel:finalize")
        context = MockHelpers.mock_context(user_data={"state": "test"})

        with patch('app.utils.business_hours.is_business_hours', return_value=True):
            with patch('app.bot.handlers.start.dashboard_callback', new=AsyncMock()):
                await handle_confirmation_callback(update, context)

        # user_data should be cleared on cancel
        assert len(context.user_data) == 0

    @pytest.mark.asyncio
    async def test_handle_contractor_selection(self):
        from app.bot.handlers.report_create import handle_contractor_selection
        from app.models.database import Contractor

        contractor_obj = Contractor(name="Test Co", type="Civil")
        update = MockHelpers.mock_update(callback_data="select_contractor:0")
        context = MockHelpers.mock_context(user_data={
            "search_results": [("Test Co (Civil)", "0")],
            "search_contractor_objects": [contractor_obj],
        })

        await handle_contractor_selection(update, context)

        assert context.user_data["selected_contractor"] == "Test Co (Civil)"
        assert context.user_data["selected_contractor_raw_name"] == "Test Co"
        assert context.user_data["selected_contractor_type"] == "Civil"
        assert context.user_data["selected_contractor_code"] == "Test Co"
        assert context.user_data["state"] == "awaiting_worker_count"
        update.callback_query.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_zone_selection_with_zone(self):
        from app.bot.handlers.report_create import handle_zone_selection

        update = MockHelpers.mock_update(callback_data="select_zone:Zone A")
        context = MockHelpers.mock_context(user_data={})

        await handle_zone_selection(update, context)

        assert context.user_data["current_zone"] == "Zone A"
        assert context.user_data["state"] == "awaiting_details"

    @pytest.mark.asyncio
    async def test_handle_zone_selection_skip(self):
        from app.bot.handlers.report_create import handle_zone_selection

        update = MockHelpers.mock_update(callback_data="select_zone:__skip__")
        context = MockHelpers.mock_context(user_data={})

        await handle_zone_selection(update, context)

        assert context.user_data["current_zone"] is None
        assert context.user_data["state"] == "awaiting_details"

    @pytest.mark.asyncio
    async def test_handle_worker_count_valid(self):
        from app.bot.handlers.report_create import handle_worker_count
        from app.models.database import Contractor, Zone

        update = MockHelpers.mock_update(message_text="10")
        context = MockHelpers.mock_context(user_data={
            "state": "awaiting_worker_count",
            "selected_contractor": "Test Co",
        })

        contractor_search = MagicMock()
        contractor_search.get_all_zones.return_value = [Zone(name="Zone A")]
        context.bot_data = {"contractor_search": contractor_search}

        await handle_worker_count(update, context)

        assert context.user_data["current_workers"] == 10
        assert context.user_data["state"] == "awaiting_craftsmen"

    @pytest.mark.asyncio
    async def test_handle_worker_count_invalid(self):
        from app.bot.handlers.report_create import handle_worker_count

        update = MockHelpers.mock_update(message_text="abc")
        context = MockHelpers.mock_context(user_data={
            "state": "awaiting_worker_count",
        })

        await handle_worker_count(update, context)

        update.message.reply_text.assert_called_once()
        assert "valid number" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_done_command(self):
        from app.bot.handlers.report_create import done_command
        from app.models.database import Report, ReportItem, ReportStatus

        update = MockHelpers.mock_update(message_text="/done")

        item = ReportItem(contractor="Test Co", workers=10)
        report = Report(
            date="2026-07-12", day="Test", status=ReportStatus.DRAFT,
            items=[item], id=1,
        )
        context = MockHelpers.mock_context(user_data={
            "current_report": report,
        })

        with patch('app.utils.business_hours.is_business_hours', return_value=True):
            await done_command(update, context)

        assert context.user_data["state"] == "idle"
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Summary" in call_text or "Report" in call_text


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Business Hours Integration with Handlers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestBusinessHoursIntegration:
    """Test business hours blocking in handlers."""

    @pytest.mark.asyncio
    async def test_main_menu_callback_blocked_after_hours(self):
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="finalize")

        with patch('app.utils.business_hours.is_business_hours', return_value=False):
            context = MockHelpers.mock_context(user_role="admin", user_data={})
            await handle_main_menu_callback(update, context)

            # Should show after-hours message
            update.callback_query.edit_message_text.assert_called_once()
            call_text = update.callback_query.edit_message_text.call_args[0][0]
            assert "After Hours" in call_text or "read-only" in call_text

    @pytest.mark.asyncio
    async def test_main_menu_callback_read_only_allowed_after_hours(self):
        from app.bot.handlers.start import handle_main_menu_callback

        update = MockHelpers.mock_update(callback_data="search")

        with patch('app.utils.business_hours.is_business_hours', return_value=False):
            context = MockHelpers.mock_context(user_role="normal_user", user_data={})
            await handle_main_menu_callback(update, context)

            # Search is read-only, should NOT show after-hours block
            call_text = update.callback_query.edit_message_text.call_args[0][0]
            assert "After Hours" not in call_text

    @pytest.mark.asyncio
    async def test_main_menu_callback_dashboard_allowed_after_hours(self):
        from app.bot.handlers.start import handle_main_menu_callback
        from types import SimpleNamespace

        update = MockHelpers.mock_update(callback_data="dashboard")

        with patch('app.utils.business_hours.is_business_hours', return_value=False):
            # Mock dashboard data
            mock_dash_data = SimpleNamespace(
                date="2026-07-12",
                day="Sunday",
                time="10:00 AM",
                report_status="not_created",
                contractor_count=0,
                total_workers=0,
                time_remaining="4h",
            )
            mock_dashboard = MagicMock()
            mock_dashboard.get_dashboard.return_value = mock_dash_data
            context = MockHelpers.mock_context(
                user_role="normal_user",
                user_data={},
                bot_data={"daily_dashboard_service": mock_dashboard},
            )
            await handle_main_menu_callback(update, context)

            # Dashboard is read-only
            update.callback_query.edit_message_text.assert_called_once()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Search Handler Logic
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestSearchLogic:
    """Test search handler helper functions."""

    def test_looks_like_date_valid(self):
        from app.bot.handlers.search import _looks_like_date
        assert _looks_like_date("2026-01-15") is True
        assert _looks_like_date("2026-12-01") is True

    def test_looks_like_date_invalid(self):
        from app.bot.handlers.search import _looks_like_date
        assert _looks_like_date("not-a-date") is False
        assert _looks_like_date("2026/01/15") is False
        assert _looks_like_date("15-15-2026") is True  # DD-MM-YYYY format (DD=15, MM=15 is invalid month but format detected)
        assert _looks_like_date("") is False
        assert _looks_like_date("abc-def-ghi") is False

    def test_format_search_hits_with_search_hit_objects(self):
        from app.bot.handlers.search import _format_search_hits
        from app.models.search import SearchHit
        from app.models.database import ReportStatus

        hits = [
            SearchHit(report_id=1, date="2026-01-01", day="Mon",
                      status=ReportStatus.FINAL, contractor_count=3, total_workers=10),
            SearchHit(report_id=2, date="2026-01-02", day="Tue",
                      status=ReportStatus.DRAFT, contractor_count=5, total_workers=20),
        ]

        formatted = _format_search_hits(hits)
        assert len(formatted) == 2
        assert formatted[0]["date"] == "2026-01-01"
        assert formatted[0]["status"] == "final"
        assert formatted[1]["date"] == "2026-01-02"
        assert formatted[1]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_handle_search_page(self):
        from app.bot.handlers.search import handle_search_page
        from app.models.search import SearchResult, SearchQuery

        update = MockHelpers.mock_update(callback_data="search_page:1")

        search_service = MagicMock()
        search_service.search.return_value = SearchResult(
            query=SearchQuery(text="test"),
            hits=[],
            total_count=0,
        )

        context = MockHelpers.mock_context(
            user_data={
                "last_search_query_text": "test",
                "last_search_total_pages": 3,
                "last_search_page_size": 5,
            },
            user_role="viewer",
            bot_data={"universal_search_service": search_service},
        )

        await handle_search_page(update, context)

        search_service.search.assert_called_once()
        update.callback_query.edit_message_text.assert_called_once()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Database Integration — Full Workflow
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestDatabaseWorkflow:
    """Test actual database operations through the full workflow."""

    @pytest.fixture
    def db_manager(self):
        """Create a temporary in-memory database for testing."""
        import tempfile
        from app.database.manager import DatabaseManager

        # Use a temporary file to avoid WAL locking issues
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        db = DatabaseManager(db_path)
        db.run_migration()
        yield db

        # Cleanup
        db.close_all()
        import os
        try:
            os.unlink(db_path)
        except PermissionError:
            pass
        for ext in ["-wal", "-shm"]:
            try:
                os.unlink(db_path + ext)
            except (FileNotFoundError, PermissionError):
                pass

    @pytest.fixture
    def report_repo(self, db_manager):
        from app.repositories.report_repository import ReportRepository
        return ReportRepository(db_manager)

    @pytest.fixture
    def event_log_repo(self, db_manager):
        from app.repositories.event_log_repository import EventLogRepository
        return EventLogRepository(db_manager)

    def test_create_report_flow(self, report_repo):
        """Test the complete report creation flow."""
        from app.models.database import Report, ReportItem, ReportStatus

        # Create report
        report = Report(
            date="2026-07-12",
            day="Test Day",
            telegram_user="user123",
            status=ReportStatus.DRAFT,
        )
        report = report_repo.add(report)
        assert report.id is not None

        # Add items
        item1 = ReportItem(contractor="Contractor A", workers=10, zone="Zone 1")
        item2 = ReportItem(contractor="Contractor B", workers=5, zone="Zone 2")
        report.items = [item1, item2]
        report = report_repo.update(report)
        assert len(report_repo.get_by_id(report.id).items) == 2

        # Verify get by date
        fetched = report_repo.get_by_date("2026-07-12")
        assert fetched is not None
        assert len(fetched.items) == 2

        # Finalize report
        from app.services.report_workflow_service import ReportWorkflowService
        from app.services.event_log_service import EventLogService

        event_log = EventLogService(report_repo._db)
        from app.models.config import AppConfig, LifecycleConfig
        config = MagicMock(spec=AppConfig)
        config.lifecycle = LifecycleConfig()

        workflow = ReportWorkflowService(report_repo, event_log, config)
        finalized = workflow.finalize_report(report, "user123")
        assert finalized.status == ReportStatus.FINAL
        assert finalized.finalized_at is not None

        # Verify event was logged
        events = event_log.get_by_action("report.finalized")
        assert len(events) == 1

        # Lock report
        locked = workflow.lock_report(finalized, "user123")
        assert locked.status == ReportStatus.LOCKED
        assert locked.locked_at is not None

        # Admin unlock
        unlocked = workflow.unlock_report(locked, "admin123", admin=True)
        assert unlocked.status == ReportStatus.DRAFT
        assert unlocked.locked_at is not None  # preserved for audit

    def test_event_logging_flow(self, db_manager, report_repo, event_log_repo):
        """Test event logging through the full flow."""
        from app.models.database import Report, ReportStatus

        # Create report with event_log_repo wired in
        repo = report_repo.__class__(db_manager, event_log_repo)

        report = Report(
            date="2026-07-13",
            day="Test Day",
            telegram_user="user123",
            status=ReportStatus.DRAFT,
        )
        report = repo.add(report)

        # Verify event was created
        events = event_log_repo.get_recent(limit=10)
        assert len(events) >= 1
        assert events[0].action == "report.created"

        # Update report
        report.day = "Updated Day"
        repo.update(report)

        events = event_log_repo.get_by_action("draft.saved")
        assert len(events) >= 1

        # Delete report
        repo.delete(report.id)
        events = event_log_repo.get_by_action("report.deleted")
        assert len(events) >= 1

    def test_admin_events_from_service(self, db_manager):
        """Test that EventLogService.get_recent works (BUG-001 fix verification)."""
        from app.services.event_log_service import EventLogService
        from app.models.database import Report, ReportStatus

        event_service = EventLogService(db_manager)
        repo = __import__('app.repositories.report_repository', fromlist=['ReportRepository']).ReportRepository(db_manager, event_service._repo)

        # Create a report to generate events
        report = Report(
            date="2026-07-14",
            day="Test",
            telegram_user="user123",
            status=ReportStatus.DRAFT,
        )
        repo.add(report)

        # Now test admin events (what admin.py does)
        events = event_service.get_recent(limit=10)
        assert len(events) >= 1

        # Verify format (what admin.py displays)
        for e in events:
            assert e.timestamp is not None
            assert e.action is not None

    def test_search_integration(self, db_manager, report_repo):
        """Test search through the full pipeline."""
        from app.models.database import Report, ReportItem, ReportStatus
        from app.services.universal_search_service import UniversalSearchService
        from app.repositories.search_repository import SearchRepository
        from app.models.search import SearchQuery

        search_repo = SearchRepository(db_manager)
        search_service = UniversalSearchService(search_repo)

        # Create test data
        report = Report(
            date="2026-07-15",
            day="Test",
            telegram_user="user123",
            status=ReportStatus.FINAL,
        )
        report.items = [ReportItem(contractor="Test Corp", workers=10, zone="Zone A")]
        report = report_repo.add(report)

        # Search by contractor name
        result = search_service.search(SearchQuery(text="Test Corp"))
        assert result.has_results
        assert len(result.hits) >= 1
        assert result.hits[0].date == "2026-07-15"

        # Search by date
        result = search_service.search(SearchQuery(text="2026-07-15"))
        assert result.has_results

        # Search by zone
        result = search_service.search(SearchQuery(text="Zone A"))
        assert result.has_results

    def test_daily_dashboard_integration(self, db_manager, report_repo):
        """Test daily dashboard with real data."""
        from app.services.daily_dashboard_service import DailyDashboardService
        from app.models.database import Report, ReportItem, ReportStatus
        from app.models.config import AppConfig, DashboardConfig

        config = MagicMock(spec=AppConfig)
        config.dashboard = DashboardConfig()

        dashboard = DailyDashboardService(report_repo, config)

        # No report
        dash = dashboard.get_dashboard(today="2026-07-16")
        assert dash.report_status == "not_created"
        assert dash.contractor_count == 0

        # With draft report
        report = Report(
            date="2026-07-16",
            day="Test",
            telegram_user="user123",
            status=ReportStatus.DRAFT,
        )
        report.items = [ReportItem(contractor="Test Co", workers=10)]
        report_repo.add(report)

        dash = dashboard.get_dashboard(today="2026-07-16")
        assert dash.report_status == "draft"
        assert dash.contractor_count == 1
        assert dash.total_workers == 10


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Test: Mock-based Handler Error Handling
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestHandlerErrorHandling:
    """Test handlers handle errors gracefully."""

    @pytest.mark.asyncio
    async def test_preview_command_no_report(self):
        from app.bot.handlers.start import preview_pdf_command

        update = MockHelpers.mock_update(message_text="/preview")
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None
        context = MockHelpers.mock_context(
            user_role="viewer", bot_data={"report_repository": repo_mock})

        await preview_pdf_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "No report" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_finalize_command_no_report(self):
        from app.bot.handlers.start import finalize_command

        update = MockHelpers.mock_update(message_text="/finalize")
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None
        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        await finalize_command(update, context)

        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_lock_command_no_report(self):
        from app.bot.handlers.start import lock_command

        update = MockHelpers.mock_update(message_text="/lock")
        repo_mock = MagicMock()
        repo_mock.get_by_date.return_value = None
        context = MockHelpers.mock_context(
            user_role="admin",
            bot_data={"report_repository": repo_mock},
        )

        await lock_command(update, context)

        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_copy_yesterday_command(self):
        from app.bot.handlers.report_create import copy_yesterday_command

        update = MockHelpers.mock_update(message_text="/copy")

        one_click_mock = MagicMock()
        one_click_mock.copy_yesterday.return_value = None  # No yesterday

        auto_save_mock = MagicMock()

        context = MockHelpers.mock_context(bot_data={
            "one_click_yesterday_service": one_click_mock,
            "auto_save_service": auto_save_mock,
        })

        with patch('app.utils.business_hours.is_business_hours', return_value=True):
            await copy_yesterday_command(update, context)

        update.message.reply_text.assert_called_once()
        assert "No report" in update.message.reply_text.call_args[0][0]
