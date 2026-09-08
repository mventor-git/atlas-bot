"""
Tests for Arabic Date & Time Service.

Tests cover:
- Arabic-Indic digit conversion
- Arabic date formatting
- Arabic day names for all weekdays
- Automatic date detection
- Time window validation (inside/outside/boundaries)
- Edge cases (leap years, month boundaries)
- The format_arabic_summary helper
"""

from datetime import date, datetime, timedelta, time

import pytest

from app.services.arabic_date_service import (
    ArabicDateService,
    TimeWindowResult,
)


class TestArabicDigitConversion:
    """Tests for Arabic-Indic digit conversion."""

    def test_convert_basic_digits(self):
        """Should convert 0-9 to Arabic-Indic digits."""
        result = ArabicDateService.to_arabic_digits("0123456789")
        assert result == "٠١٢٣٤٥٦٧٨٩", f"Expected Arabic digits, got '{result}'"

    def test_convert_mixed_text(self):
        """Should convert digits within text."""
        result = ArabicDateService.to_arabic_digits("Year 2026 Report")
        assert result == "Year ٢٠٢٦ Report", f"Expected 'Year ٢٠٢٦ Report', got '{result}'"

    def test_convert_no_digits(self):
        """Should return text unchanged when no digits present."""
        text = "Hello in Arabic"
        result = ArabicDateService.to_arabic_digits(text)
        assert result == text, f"Expected '{text}', got '{result}'"

    def test_convert_empty_string(self):
        """Should handle empty string."""
        assert ArabicDateService.to_arabic_digits("") == "", "Empty string should return empty string"

    def test_convert_padded_numbers(self):
        """Should convert padded numbers (01, 02, etc.)."""
        assert ArabicDateService.to_arabic_digits("01") == "٠١", "Expected '٠١' for '01'"
        assert ArabicDateService.to_arabic_digits("12") == "١٢", "Expected '١٢' for '12'"
        assert ArabicDateService.to_arabic_digits("1234") == "١٢٣٤", "Expected '١٢٣٤' for '1234'"

    def test_convert_with_leading_trailing_whitespace(self):
        """Should handle whitespace around digits."""
        result = ArabicDateService.to_arabic_digits("  2026  ")
        assert result == "  ٢٠٢٦  ", f"Expected '  ٢٠٢٦  ', got '{result}'"

    def test_convert_already_arabic_unchanged(self):
        """Should not modify already Arabic text."""
        text = "تاريخ ٢٠٢٦"
        result = ArabicDateService.to_arabic_digits(text)
        assert result == text, f"Expected '{text}', got '{result}'"


class TestArabicDateFormatting:
    """Tests for Arabic date formatting."""

    def test_format_today_default(self):
        """Should format today's date when no date provided."""
        result = ArabicDateService.get_arabic_date()
        today = date.today()
        expected_day = ArabicDateService.to_arabic_digits(f"{today.day:02d}")
        expected_month = ArabicDateService.to_arabic_digits(f"{today.month:02d}")
        expected_year = ArabicDateService.to_arabic_digits(f"{today.year:04d}")
        expected = f"{expected_day} / {expected_month} / {expected_year}"
        assert result == expected, f"Expected '{expected}', got '{result}'"

    def test_format_specific_date(self):
        """Should format a specific date correctly."""
        d = date(2026, 8, 15)
        result = ArabicDateService.get_arabic_date(d)
        assert result == "١٥ / ٠٨ / ٢٠٢٦", f"Expected '١٥ / ٠٨ / ٢٠٢٦', got '{result}'"

    def test_format_with_custom_separator(self):
        """Should accept custom separator."""
        d = date(2026, 8, 15)
        result = ArabicDateService.get_arabic_date(d, separator="-")
        assert result == "١٥-٠٨-٢٠٢٦", f"Expected '١٥-٠٨-٢٠٢٦', got '{result}'"

    def test_format_leap_year_february(self):
        """Should handle leap year dates."""
        d = date(2024, 2, 29)  # Leap year
        result = ArabicDateService.get_arabic_date(d)
        assert "٢٩" in result, f"Expected '٢٩' in result, got '{result}'"
        assert "٠٢" in result, f"Expected '٠٢' in result, got '{result}'"

    def test_format_new_year(self):
        """Should handle January 1st."""
        d = date(2026, 1, 1)
        result = ArabicDateService.get_arabic_date(d)
        assert result == "٠١ / ٠١ / ٢٠٢٦", f"Expected '٠١ / ٠١ / ٢٠٢٦', got '{result}'"

    def test_format_end_of_year(self):
        """Should handle December 31st."""
        d = date(2026, 12, 31)
        result = ArabicDateService.get_arabic_date(d)
        assert result == "٣١ / ١٢ / ٢٠٢٦", f"Expected '٣١ / ١٢ / ٢٠٢٦', got '{result}'"

    def test_format_extreme_dates(self):
        """Should handle year 1 and year 9999 dates."""
        d1 = date(1, 1, 1)
        result1 = ArabicDateService.get_arabic_date(d1)
        assert "٠١" in result1, f"Year 1 date should have digits, got '{result1}'"

        d2 = date(9999, 12, 31)
        result2 = ArabicDateService.get_arabic_date(d2)
        assert "٣١" in result2, f"Year 9999 date should have digits, got '{result2}'"

    def test_format_with_empty_separator(self):
        """Should handle empty separator."""
        d = date(2026, 8, 15)
        result = ArabicDateService.get_arabic_date(d, separator="")
        expected = "15082026"
        # Compare digit-converted version
        assert ArabicDateService.to_arabic_digits(expected) == result, f"Expected digits of '{expected}', got '{result}'"


class TestArabicDayNames:
    """Tests for Arabic day name calculation."""

    def test_saturday(self):
        """Saturday should be 'السبت'."""
        d = date(2026, 7, 11)  # This is a Saturday in 2026
        # Actually let me verify: 2026-07-11... 
        # July 11, 2026 = Saturday? Let me use a known date.
        # 2026-01-01 is Thursday
        # Actually let me use a reliable reference:
        # 2026-07-11 is a Saturday
        name = ArabicDateService.get_arabic_day_name(d)
        assert name == "السبت", f"Expected السبت for 2026-07-11, got {name}"

    def test_sunday(self):
        """Sunday should be 'الأحد'."""
        d = date(2026, 7, 12)
        name = ArabicDateService.get_arabic_day_name(d)
        assert name == "الأحد", f"Expected الأحد for 2026-07-12, got {name}"

    def test_monday(self):
        """Monday should be 'الاثنين'."""
        d = date(2026, 7, 13)
        name = ArabicDateService.get_arabic_day_name(d)
        assert name == "الاثنين", f"Expected الاثنين for 2026-07-13, got {name}"

    def test_tuesday(self):
        """Tuesday should be 'الثلاثاء'."""
        d = date(2026, 7, 14)
        name = ArabicDateService.get_arabic_day_name(d)
        assert name == "الثلاثاء", f"Expected الثلاثاء for 2026-07-14, got {name}"

    def test_wednesday(self):
        """Wednesday should be 'الأربعاء'."""
        d = date(2026, 7, 15)
        name = ArabicDateService.get_arabic_day_name(d)
        assert name == "الأربعاء", f"Expected الأربعاء for 2026-07-15, got {name}"

    def test_thursday(self):
        """Thursday should be 'الخميس'."""
        d = date(2026, 7, 16)
        name = ArabicDateService.get_arabic_day_name(d)
        assert name == "الخميس", f"Expected الخميس for 2026-07-16, got {name}"

    def test_friday(self):
        """Friday should be 'الجمعة'."""
        d = date(2026, 7, 17)
        name = ArabicDateService.get_arabic_day_name(d)
        assert name == "الجمعة", f"Expected الجمعة for 2026-07-17, got {name}"

    def test_default_is_today(self):
        """Should return today's day name by default."""
        result = ArabicDateService.get_arabic_day_name()
        today = date.today()
        expected = ArabicDateService.get_arabic_day_name(today)
        assert result == expected, f"Expected '{expected}', got '{result}'"

    def test_all_weekdays_covered(self):
        """Should return correct names for all 7 weekdays in sequence."""
        # Start from a known Monday (2026-07-13)
        start = date(2026, 7, 13)  # Monday
        expected_names = [
            "الاثنين",   # Monday
            "الثلاثاء",  # Tuesday
            "الأربعاء",  # Wednesday
            "الخميس",    # Thursday
            "الجمعة",    # Friday
            "السبت",     # Saturday
            "الأحد",     # Sunday
        ]
        for i, expected in enumerate(expected_names):
            d = start + timedelta(days=i)
            name = ArabicDateService.get_arabic_day_name(d)
            assert name == expected, f"Failed for {d}: expected {expected}, got {name}"


class TestTimeWindow:
    """Tests for time window validation."""

    def test_within_window_morning(self):
        """Should return is_within_window=True at 09:00."""
        t = time(9, 0)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is True, f"Expected True at 09:00, got {result}"

    def test_within_window_mid(self):
        """Should return is_within_window=True at 12:00."""
        t = time(12, 0)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is True, f"Expected True at 12:00, got {result}"

    def test_within_window_end(self):
        """Should return is_within_window=True at 14:00."""
        t = time(14, 0)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is True, f"Expected True at 14:00, got {result}"

    def test_before_window(self):
        """Should return is_within_window=False at 08:59."""
        t = time(8, 59)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is False, f"Expected False at 08:59, got {result}"
        assert "opens at" in result.message, f"Expected 'opens at' in message, got '{result.message}'"

    def test_after_window(self):
        """Should return is_within_window=False at 14:01."""
        t = time(14, 1)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is False, f"Expected False at 14:01, got {result}"
        assert "closed" in result.message, f"Expected 'closed' in message, got '{result.message}'"

    def test_early_morning(self):
        """Should be outside window at 06:00."""
        t = time(6, 0)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is False, f"Expected False at 06:00, got {result}"

    def test_evening(self):
        """Should be outside window at 20:00."""
        t = time(20, 0)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is False, f"Expected False at 20:00, got {result}"

    def test_midnight(self):
        """Should be outside window at 00:00."""
        t = time(0, 0)
        result = ArabicDateService.check_time_window(t)
        assert result.is_within_window is False, f"Expected False at 00:00, got {result}"

    def test_window_boundaries_inclusive(self):
        """Window boundaries should be inclusive (09:00 and 14:00)."""
        assert ArabicDateService.check_time_window(time(9, 0)).is_within_window is True, "09:00 should be within window"
        assert ArabicDateService.check_time_window(time(14, 0)).is_within_window is True, "14:00 should be within window"
        assert ArabicDateService.check_time_window(time(8, 59)).is_within_window is False, "08:59 should be outside window"
        assert ArabicDateService.check_time_window(time(14, 1)).is_within_window is False, "14:01 should be outside window"

    def test_current_time_in_result(self):
        """Should include current time string in result."""
        t = time(10, 30)
        result = ArabicDateService.check_time_window(t)
        assert result.current_time == "10:30", f"Expected '10:30', got '{result.current_time}'"

    def test_window_times_in_result(self):
        """Should include window start and end times in result."""
        result = ArabicDateService.check_time_window(time(12, 0))
        assert result.window_start == "09:00", f"Expected '09:00', got '{result.window_start}'"
        assert result.window_end == "14:00", f"Expected '14:00', got '{result.window_end}'"

    def test_custom_window_bounds(self):
        """Should respect custom window_start and window_end parameters."""
        result = ArabicDateService.check_time_window(time(20, 0), window_start=time(19, 0), window_end=time(22, 0))
        assert result.is_within_window is True, f"Expected True at 20:00 with custom window 19-22"
        assert result.window_start == "19:00"
        assert result.window_end == "22:00"

        # Outside custom window
        result2 = ArabicDateService.check_time_window(time(23, 0), window_start=time(19, 0), window_end=time(22, 0))
        assert result2.is_within_window is False, f"Expected False at 23:00 with custom window 19-22"


class TestArabicSummary:
    """Tests for formatted Arabic summary."""

    def test_summary_format(self):
        """Should combine day name and Arabic date."""
        d = date(2026, 7, 11)  # Saturday
        summary = ArabicDateService.format_arabic_summary(d)
        assert "السبت" in summary, f"Expected 'السبت' in summary, got '{summary}'"
        assert "١١" in summary, f"Expected '١١' in summary, got '{summary}'"
        assert "٠٧" in summary, f"Expected '٠٧' in summary, got '{summary}'"

    def test_summary_defaults_to_today(self):
        """Should use today by default."""
        summary = ArabicDateService.format_arabic_summary()
        today = date.today()
        day_name = ArabicDateService.get_arabic_day_name(today)
        assert day_name in summary, f"Expected '{day_name}' in summary '{summary}'"
        assert len(summary) > 5, f"Summary too short: '{summary}'"


class TestTodayDetection:
    """Tests for today detection."""

    def test_is_today_true(self):
        """Should return True for today's date."""
        today_str = date.today().isoformat()
        assert ArabicDateService.is_today(today_str) is True, f"Expected True for today '{today_str}'"

    def test_is_today_false(self):
        """Should return False for another date."""
        assert ArabicDateService.is_today("2020-01-01") is False, "Expected False for past date"

    def test_is_today_invalid_format(self):
        """Should return False for invalid date string."""
        assert ArabicDateService.is_today("not-a-date") is False, "Expected False for invalid string"
        assert ArabicDateService.is_today("") is False, "Expected False for empty string"
        assert ArabicDateService.is_today(None) is False, "Expected False for None"  # type: ignore


class TestTimeWindowResult:
    """Tests for TimeWindowResult dataclass."""

    def test_result_creation(self):
        """Should create TimeWindowResult with all fields."""
        result = TimeWindowResult(
            is_within_window=True,
            current_time="10:00",
            window_start="09:00",
            window_end="14:00",
            message="Window is open.",
        )
        assert result.is_within_window is True, "Expected is_within_window=True"
        assert result.current_time == "10:00", f"Expected '10:00', got '{result.current_time}'"
        assert result.window_start == "09:00", f"Expected '09:00', got '{result.window_start}'"
        assert result.window_end == "14:00", f"Expected '14:00', got '{result.window_end}'"
        assert result.message == "Window is open.", f"Expected 'Window is open.', got '{result.message}'"


class TestGetTodayAndNow:
    """Tests for date/time retrieval methods."""

    def test_get_today_returns_date(self):
        """Should return a date object for today."""
        result = ArabicDateService.get_today()
        assert isinstance(result, date), f"Expected date, got {type(result)}"
        assert result == date.today(), f"Expected {date.today()}, got {result}"

    def test_get_now_returns_datetime(self):
        """Should return a datetime object."""
        result = ArabicDateService.get_now()
        assert isinstance(result, datetime), f"Expected datetime, got {type(result)}"

    def test_get_current_time_returns_time(self):
        """Should return a time object."""
        result = ArabicDateService.get_current_time()
        assert isinstance(result, time), f"Expected time, got {type(result)}"
