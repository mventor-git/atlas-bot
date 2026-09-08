"""
Date parser utility — parses date strings in multiple formats.

Supported input formats:
  - YYYY-MM-DD       (2026-07-12)
  - YYYY/MM/DD       (2026/07/12)
  - DD-MM-YYYY       (12-07-2026)
  - DD/MM/YYYY       (12/07/2026)
  - "yesterday"      (English)
  - "امس"            (Arabic — yesterday)
  - "اليوم"          (Arabic — today)

Usage:
    from app.utils.date_parser import parse_date_string
    dt = parse_date_string("12-07-2026")   → date(2026, 7, 12)
    dt = parse_date_string("yesterday")     → date(2026, 7, 11) (if today is 2026-07-12)
    dt = parse_date_string("bad input")     → None
"""

import re
from datetime import date, timedelta
from typing import Optional


def parse_date_string(text: str, today: Optional[date] = None) -> Optional[date]:
    """Parse a date string in various formats.

    Args:
        text: The user's input string.
        today: Reference date for "yesterday"/"today" keywords.
               Defaults to ``date.today()``.

    Returns:
        A ``date`` object if parsing succeeds, ``None`` otherwise.
    """
    if not text or not text.strip():
        return None

    text = text.strip()
    if today is None:
        today = date.today()

    # ── Keywords ────────────────────────────────────────────────

    if text.lower() in ("yesterday", "yest", "yersterday"):
        return today - timedelta(days=1)

    if text in ("امس", "أمس"):
        return today - timedelta(days=1)

    if text.lower() in ("today", "اليوم"):
        return today

    # ── Pattern: YYYY-MM-DD or YYYY/MM/DD ───────────────────────

    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _try_date(year, month, day)

    # ── Pattern: DD-MM-YYYY or DD/MM/YYYY ───────────────────────

    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _try_date(year, month, day)

    # ── Pattern: YYYYMMDD (compact) ─────────────────────────────

    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _try_date(year, month, day)

    # ── Pattern: DD.MM.YYYY (European dot format) ───────────────

    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _try_date(year, month, day)

    return None


def _try_date(year: int, month: int, day: int) -> Optional[date]:
    """Try to construct a date, validating month/day ranges.

    Args:
        year: Year (e.g., 2026).
        month: Month (1-12).
        day: Day (1-31).

    Returns:
        A ``date`` object if valid, ``None`` otherwise.
    """
    try:
        return date(year, month, day)
    except (ValueError, OverflowError):
        return None


def is_likely_date(text: str) -> bool:
    """Quick check if a text string looks like a date reference.

    Checks for digit-heavy patterns or known keywords.
    Useful for routing in catch-all text handlers.

    Args:
        text: The input string.

    Returns:
        True if the text looks like a date.
    """
    if not text or not text.strip():
        return False

    text = text.strip().lower()

    # Keywords
    if text in ("yesterday", "yest", "yersterday", "today", "امس", "أمس", "اليوم"):
        return True

    # Contains digits + separators (-, /, .)
    if re.search(r"\d{2}[-/\.]\d{2}[-/\.]\d{2,4}", text):
        return True

    # Compact YYYYMMDD
    if re.match(r"^\d{8}$", text):
        return True

    return False
