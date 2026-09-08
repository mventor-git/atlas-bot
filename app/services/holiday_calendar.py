"""
Holiday Calendar Service — Determines if a given date is a holiday (non-working day).

Sources:
- Egyptian national holidays from config/egypt_holidays.json (2025-2027)
- Friday is always a non-working day (weekend in Egypt)
- Islamic holiday dates are approximate; actual dates depend on moon sighting

Usage:
    service = HolidayCalendarService()
    if service.is_holiday("2026-01-25"):
        print("Holiday!")
    if service.is_working_day("2026-07-12"):
        print("Working day")
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from app.models.config import AppConfig

logger = logging.getLogger(__name__)

# Default holiday JSON path relative to project root
DEFAULT_HOLIDAYS_PATH = Path("config/egypt_holidays.json")


class HolidayCalendarService:
    """Service for checking Egyptian holidays and non-working days.

    Automatically considers:
      - Every Friday as a non-working day (Egyptian weekend)
      - All national holidays loaded from JSON calendar file
    """

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        """Initialize the holiday calendar service.

        Args:
            config: Optional app config (for custom holiday file path and
                    holiday enforcement toggle).
                    Falls back to config/egypt_holidays.json.
        """
        self._config = config
        # Load holidays: set of date strings in 'YYYY-MM-DD' format
        self._holidays: set[str] = set()
        # Detailed holiday names keyed by date
        self._holiday_names: dict[str, str] = {}

        # Check if holiday/Friday enforcement is enabled
        self._enforce_holidays = True
        if config is not None:
            tz_config = getattr(config, "timezone", None)
            if tz_config is not None:
                val = getattr(tz_config, "holidays_and_friday", "enabled")
                self._enforce_holidays = val.lower() == "enabled"

        self._load_holidays()

    # ─── Public API ─────────────────────────────────────────────

    def is_holiday(self, date_or_str: str | date | datetime) -> bool:
        """Check if a date is a holiday or Friday.

        When ``holidays_and_friday`` config is ``"disabled"``, returns
        ``False`` for all dates — the bot treats every day as a normal
        working day.

        Args:
            date_or_str: Date string (YYYY-MM-DD), date, or datetime.

        Returns:
            True if the date is a holiday or Friday (when enforcement is enabled).
        """
        if not self._enforce_holidays:
            return False

        date_str = self._to_date_str(date_or_str)
        if not date_str:
            return False

        # Check Friday (weekend in Egypt)
        if self._is_friday(date_str):
            return True

        # Check loaded holidays
        return date_str in self._holidays

    def is_working_day(self, date_or_str: str | date | datetime) -> bool:
        """Check if a date is a normal working day (not holiday, not Friday).

        Args:
            date_or_str: Date string (YYYY-MM-DD), date, or datetime.

        Returns:
            True if the date is a working day.
        """
        return not self.is_holiday(date_or_str)

    def get_holiday_name(self, date_or_str: str | date | datetime) -> Optional[str]:
        """Get the name of a holiday for a given date, if it's a holiday.

        For Fridays that are not a named holiday, returns 'الجمعة (Friday)'.

        Args:
            date_or_str: Date string (YYYY-MM-DD), date, or datetime.

        Returns:
            Holiday name string, or None if it's a working day.
        """
        date_str = self._to_date_str(date_or_str)
        if not date_str:
            return None

        # Check named holidays first
        if date_str in self._holiday_names:
            return self._holiday_names[date_str]

        # Check Friday
        if self._is_friday(date_str):
            return "الجمعة (Friday - Weekend)"

        return None

    def get_holidays_in_range(
        self, start: str | date, end: str | date
    ) -> list[dict]:
        """Get all holidays in a date range (inclusive).

        Args:
            start: Start date (YYYY-MM-DD or date).
            end: End date (YYYY-MM-DD or date).

        Returns:
            List of {'date': str, 'name': str, 'type': str} dicts.
        """
        start_str = self._to_date_str(start)
        end_str = self._to_date_str(end)

        if not start_str or not end_str:
            return []

        results: list[dict] = []
        try:
            current = date.fromisoformat(start_str)
            end_date = date.fromisoformat(end_str)

            while current <= end_date:
                current_str = current.isoformat()
                if self.is_holiday(current_str):
                    name = self.get_holiday_name(current_str) or "Holiday"
                    # Determine holiday type
                    htype = "weekend"
                    if current_str in self._holiday_names:
                        # Check if national or other
                        htype = "national"
                    results.append({
                        "date": current_str,
                        "name": name,
                        "type": htype,
                    })
                current = date.fromordinal(current.toordinal() + 1)
        except ValueError:
            logger.warning("Invalid date range: %s to %s", start, end)

        return results

    def is_friday(self, date_or_str: str | date | datetime) -> bool:
        """Check if a date is a Friday.

        Args:
            date_or_str: Date string (YYYY-MM-DD), date, or datetime.

        Returns:
            True if the date is a Friday.
        """
        return self._is_friday(self._to_date_str(date_or_str))

    def next_working_day(self, from_date: str | date) -> Optional[str]:
        """Get the next working day after a given date.

        Args:
            from_date: Starting date (YYYY-MM-DD or date).

        Returns:
            Next working day as YYYY-MM-DD string, or None if error.
        """
        from_str = self._to_date_str(from_date)
        if not from_str:
            return None

        try:
            current = date.fromisoformat(from_str)
            for _ in range(30):  # Safety limit
                current = date.fromordinal(current.toordinal() + 1)
                if self.is_working_day(current.isoformat()):
                    return current.isoformat()
        except ValueError:
            pass

        return None

    def previous_working_day(self, from_date: str | date) -> Optional[str]:
        """Get the previous working day before a given date.

        Args:
            from_date: Starting date (YYYY-MM-DD or date).

        Returns:
            Previous working day as YYYY-MM-DD string, or None if error.
        """
        from_str = self._to_date_str(from_date)
        if not from_str:
            return None

        try:
            current = date.fromisoformat(from_str)
            for _ in range(30):  # Safety limit
                current = date.fromordinal(current.toordinal() - 1)
                if self.is_working_day(current.isoformat()):
                    return current.isoformat()
        except ValueError:
            pass

        return None

    # ─── Internal helpers ───────────────────────────────────────

    def _load_holidays(self) -> None:
        """Load holidays from the JSON file."""
        holiday_path = DEFAULT_HOLIDAYS_PATH

        # Allow config override
        if self._config:
            config_path = getattr(self._config, "holiday_calendar_file", None)
            if config_path:
                holiday_path = Path(config_path)

        if not holiday_path.exists():
            logger.warning(
                "Holiday calendar file not found at %s. "
                "Only Friday will be treated as non-working day.",
                holiday_path,
            )
            return

        try:
            with open(holiday_path, encoding="utf-8") as f:
                data = json.load(f)

            holidays_dict = data.get("holidays", {})

            for year_str, year_data in holidays_dict.items():
                for category in ("fixed", "islamic", "substitute"):
                    entries = year_data.get(category, [])
                    for entry in entries:
                        date_str = entry.get("date", "")
                        name = entry.get("name", "Holiday")
                        if date_str:
                            self._holidays.add(date_str)
                            self._holiday_names[date_str] = name

            logger.info(
                "Loaded %d holiday dates from %s",
                len(self._holidays),
                holiday_path,
            )
            if self._holidays:
                # Log a few sample holidays
                sample = sorted(self._holidays)[:3]
                logger.debug("Sample holidays: %s", sample)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error("Failed to load holiday calendar: %s", e)
            self._holidays.clear()
            self._holiday_names.clear()

    @staticmethod
    def _is_friday(date_str: Optional[str]) -> bool:
        """Check if a date string falls on a Friday.

        Args:
            date_str: Date string in YYYY-MM-DD format.

        Returns:
            True if the date is a Friday.
        """
        if not date_str:
            return False
        try:
            dt = date.fromisoformat(date_str)
            # Monday=0, Sunday=6 → Friday=4
            return dt.weekday() == 4
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _to_date_str(value: str | date | datetime) -> Optional[str]:
        """Convert various date representations to YYYY-MM-DD string.

        Args:
            value: Date string, date, or datetime object.

        Returns:
            Date string in YYYY-MM-DD format, or None if invalid.
        """
        if isinstance(value, (date, datetime)):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, str):
            # Validate the string is a proper date
            try:
                date.fromisoformat(value)
                return value
            except (ValueError, TypeError):
                return None
        return None

    def __len__(self) -> int:
        """Return the number of loaded holiday dates."""
        return len(self._holidays)

    def __contains__(self, date_str: str) -> bool:
        """Check if a date string is a holiday (supports 'in' operator)."""
        return self.is_holiday(date_str)
