"""Working calendar policy (028, Stage 1): one authoritative answer to
"is this date a required workday for this site?".

Layers, in precedence order:
1. Global toggle off (``holidays_and_friday: disabled``) -> everything required.
2. Site ``working_dates`` (explicit ON dates: exceptional Fridays, make-up
   days) -> required even on weekends/holidays.
3. Site ``off_dates`` (site-specific days off) -> not required.
4. Site ``weekend`` (weekday ints, default [4] = Friday) -> not required.
5. National holidays (HolidayCalendarService data) -> not required.
6. Otherwise required.

Sites come from the untyped ``config.sites`` list (dicts), so per-site keys
ride without model changes. Date-keyed data is inherently future-dated:
adding next year's exceptions is a config edit, no code.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from app.database import driver
from app.models.config import AppConfig
from app.services.holiday_calendar import HolidayCalendarService
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_WEEKEND = (4,)  # Friday (Egypt)


class WorkingCalendar:
    """Site-aware required-workday policy over the holiday data source."""

    def __init__(self, config: Optional[AppConfig] = None,
                 site_id: str | None = None,
                 holidays: HolidayCalendarService | None = None) -> None:
        self._config = config
        self._site_id = site_id or driver.site_id()
        self._holidays = holidays or HolidayCalendarService(config)

    # --- site config ---

    def _site(self) -> dict:
        sites = getattr(self._config, "sites", None) or []
        for entry in sites:
            if isinstance(entry, dict) and str(entry.get("id")) == self._site_id:
                return entry
        return {}

    def weekend_days(self) -> list[int]:
        raw = self._site().get("weekend", None)
        if raw is None:
            return list(DEFAULT_WEEKEND)
        try:
            days = sorted({int(d) % 7 for d in raw})
        except (TypeError, ValueError):
            logger.warning("Bad weekend config for site %s; using Friday.",
                           self._site_id)
            return list(DEFAULT_WEEKEND)
        return days

    def _explicit(self, key: str, day: str) -> bool:
        raw = self._site().get(key, None) or []
        try:
            return day in {str(d) for d in raw}
        except TypeError:
            return False

    def _enforced(self) -> bool:
        return bool(getattr(self._holidays, "_enforce_holidays", True))

    # --- policy ---

    def is_required_workday(self, day: str | date | datetime) -> bool:
        """True when attendance/report output is expected for the site."""
        day_str = HolidayCalendarService._to_date_str(day)
        if not day_str:
            return False
        if self._explicit("working_dates", day_str):
            return True
        if self._explicit("off_dates", day_str):
            return False
        if not self._enforced():
            return True
        try:
            weekday = date.fromisoformat(day_str).weekday()
        except ValueError:
            return False
        if weekday in self.weekend_days():
            return False
        return not self._holidays.is_holiday(day_str)

    def describe(self, day: str | date | datetime) -> tuple[bool, str | None]:
        """(required, reason) for UX and audit trails."""
        day_str = HolidayCalendarService._to_date_str(day)
        if not day_str:
            return False, None
        if self._explicit("working_dates", day_str):
            return True, "exceptional working day"
        if self._explicit("off_dates", day_str):
            return False, "site day off"
        if not self._enforced():
            return True, "calendar disabled"
        try:
            weekday = date.fromisoformat(day_str).weekday()
        except ValueError:
            return False, None
        if weekday in self.weekend_days():
            return False, "weekend"
        name = self._holidays.get_holiday_name(day_str)
        if name:
            return False, name
        return True, "working day"
