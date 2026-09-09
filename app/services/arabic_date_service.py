"""
Arabic Date & Time Service for Labor-Report.

Provides Arabic-formatted dates, Arabic day names, time window validation,
and automatic date/time detection for the Telegram bot workflow.

Arabic-Indic digits: ٠١٢٣٤٥٦٧٨٩
Arabic day names: الأحد through السبت
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional


# Arabic-Indic digits mapping (Western → Arabic)
_ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

# English day names indexed by Python weekday() (0=Monday, 6=Sunday).
# Used for English document templates; the language switch (ar/en)
# becomes config-driven in the Arabic-templates ticket.
_ENGLISH_DAY_NAMES: dict[int, str] = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}

# Default reporting window (used when config not provided)
DEFAULT_WINDOW_START = time(9, 0)
DEFAULT_WINDOW_END = time(14, 0)


@dataclass
class TimeWindowResult:
    """Result of a time window validation check."""

    is_within_window: bool
    """True if current time is between 09:00 and 14:00."""

    current_time: str
    """Current server time formatted as HH:MM."""

    window_start: str
    """Window start time string."""

    window_end: str
    """Window end time string."""

    message: str
    """User-facing message about the time window status."""


class ArabicDateService:
    """Service for Arabic date formatting and time validation.

    Provides date-related functionality used by the Telegram bot
    and Excel template filler. All methods are stateless.

    Usage:
        service = ArabicDateService()
        arabic_date = service.get_arabic_date("2026-08-15")
        day_name = service.get_arabic_day_name("2026-08-15")
        window = service.check_time_window()
    """

    # --- Public API ---

    @staticmethod
    def get_today() -> date:
        """Get today's date based on server local time.

        Returns:
            Today's date.
        """
        return date.today()

    @staticmethod
    def get_now() -> datetime:
        """Get the current server datetime.

        Returns:
            Current datetime with no timezone (server local).
        """
        return datetime.now()

    @staticmethod
    def get_current_time() -> time:
        """Get the current server time.

        Returns:
            Current time.
        """
        return datetime.now().time()

    @staticmethod
    def to_arabic_digits(text: str) -> str:
        """Convert Western digits in a string to Arabic-Indic digits.

        Args:
            text: Text containing Western digits (0-9).

        Returns:
            Text with digits replaced by Arabic-Indic equivalents.

        Example:
            >>> ArabicDateService.to_arabic_digits("2026")
            "٢٠٢٦"
        """
        return text.translate(_ARABIC_DIGITS)

    @staticmethod
    def get_arabic_date(date_obj: Optional[date] = None, separator: str = " / ") -> str:
        """Format a date in Arabic style with Arabic-Indic digits.

        Args:
            date_obj: The date to format. If None, uses today.
            separator: Separator between day, month, year (default: " / ").

        Returns:
            Arabic-formatted date string.

        Example:
            >>> ArabicDateService.get_arabic_date(date(2026, 8, 15))
            "١٥ / ٠٨ / ٢٠٢٦"
        """
        if date_obj is None:
            date_obj = date.today()

        day = ArabicDateService.to_arabic_digits(f"{date_obj.day:02d}")
        month = ArabicDateService.to_arabic_digits(f"{date_obj.month:02d}")
        year = ArabicDateService.to_arabic_digits(f"{date_obj.year:04d}")

        return f"{day}{separator}{month}{separator}{year}"

    @staticmethod
    def get_day_name(date_obj: Optional[date] = None) -> str:
        """Get the English day name for document templates.

        Args:
            date_obj: The date. If None, uses today.

        Returns:
            English day name (e.g., 'Saturday').
        """
        if date_obj is None:
            date_obj = date.today()
        return _ENGLISH_DAY_NAMES[date_obj.weekday()]

    @staticmethod
    def get_arabic_day_name(date_obj: Optional[date] = None) -> str:
        """Get the Arabic name for the day of the week.

        Args:
            date_obj: The date. If None, uses today.

        Returns:
            Arabic day name (e.g., 'السبت', 'الأحد').

        Example:
            >>> ArabicDateService.get_arabic_day_name(date(2026, 7, 11))
            "السبت"
        """
        if date_obj is None:
            date_obj = date.today()

        # Python weekday(): 0=Monday, 6=Sunday
        # We shift so that 0=Monday aligns with our dict
        weekday = date_obj.weekday()  # 0=Monday, ..., 6=Sunday
        return _ARABIC_DAY_NAMES[weekday]

    @staticmethod
    def check_time_window(
        check_time: Optional[time] = None,
        window_start: Optional[time] = None,
        window_end: Optional[time] = None,
    ) -> TimeWindowResult:
        """Check if a given time falls within the allowed reporting window.

        The window bounds can be configured via ``window_start`` and
        ``window_end``.  When omitted they default to 09:00 and 14:00
        (server local time).

        Args:
            check_time: The time to check. If None, uses current server time.
            window_start: Start of the reporting window (default 09:00).
            window_end: End of the reporting window (default 14:00).

        Returns:
            TimeWindowResult with validation status and user-friendly message.
        """
        if check_time is None:
            check_time = datetime.now().time()
        if window_start is None:
            window_start = DEFAULT_WINDOW_START
        if window_end is None:
            window_end = DEFAULT_WINDOW_END

        is_within = window_start <= check_time <= window_end

        current_str = check_time.strftime("%H:%M")
        start_str = window_start.strftime("%H:%M")
        end_str = window_end.strftime("%H:%M")

        if is_within:
            message = f"Reporting window is open ({start_str} – {end_str}). Current time: {current_str}."
        else:
            if check_time < window_start:
                message = (
                    f"Reporting window opens at {start_str}. "
                    f"Current time: {current_str}. Please wait until {start_str}."
                )
            else:
                message = (
                    f"Reporting window closed at {end_str}. "
                    f"Current time: {current_str}. "
                    "Today's report will be auto-closed as 'No Labor'."
                )

        return TimeWindowResult(
            is_within_window=is_within,
            current_time=current_str,
            window_start=start_str,
            window_end=end_str,
            message=message,
        )

    @staticmethod
    def format_arabic_summary(date_obj: Optional[date] = None) -> str:
        """Format a complete Arabic date summary line.

        Combines Arabic date and day name into a single string.

        Args:
            date_obj: The date. If None, uses today.

        Returns:
            Formatted string like: "السبت ١٥ / ٠٨ / ٢٠٢٦"
        """
        if date_obj is None:
            date_obj = date.today()

        day_name = ArabicDateService.get_arabic_day_name(date_obj)
        arabic_date = ArabicDateService.get_arabic_date(date_obj)
        return f"{day_name} {arabic_date}"

    @staticmethod
    def is_today(date_str: str) -> bool:
        """Check if a date string matches today's date.

        Args:
            date_str: Date string in YYYY-MM-DD format.

        Returns:
            True if the date is today.
        """
        try:
            parsed = date.fromisoformat(date_str)
            return parsed == date.today()
        except (ValueError, TypeError):
            return False
