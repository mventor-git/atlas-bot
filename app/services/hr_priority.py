"""Priority engine: normal default, LOW when recommended items are missing.

Recommended lists mirror the existing required-field checks (service
raises + handler /skip flow) — read from code, not invented:
- transport: receipt photo + report ref are both skippable at filing
  (bot awaits_hr_receipt/report_ref accept /skip) -> recommended.
- advance: amount + reason are hard-required (service raises) ->
  recommended = reason text (blank/junk = missing).
- leave/mission: reason + start/end dates (service raises) -> recommended.
- overtime: date + hours + reason (service/handler raises) -> recommended.
- claim (attendance correction/note): text is the payload -> recommended.
- grievance/complaint/suggestion/resignation: summary -> recommended.
Exempt: contractor + labor reports never auto-lower, by doc type.
Filing is never blocked: LOW only sorts last + badges the card.
"""

from __future__ import annotations

from typing import Any

LOW = "low"
NORMAL = "normal"

EXEMPT_TYPES = frozenset({
    "contractor", "contractor_report", "labor_report", "report",
    "daily_report",
})

# Mirrors bot/handlers/hr.py _JUNK_REASONS (import would drag telegram deps).
_JUNK = frozenset({"no reason", "none", "n/a", "na", "-", ".", "no",
                   "skip", "test"})


def _val(doc: Any, *names: str) -> Any:
    """Read a field off a dataclass, model, or plain dict."""
    for name in names:
        if isinstance(doc, dict):
            if doc.get(name) not in (None, ""):
                return doc.get(name)
        elif getattr(doc, name, None) not in (None, ""):
            return getattr(doc, name)
    return ""


def _doc_type(doc: Any) -> str:
    return str(_val(doc, "request_type", "doc_type", "report_type",
                    "case_type", "kind", "type") or "").strip().lower()


def _text_ok(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.lower() not in _JUNK


def _missing(doc: Any, *names: str) -> bool:
    return not str(_val(doc, *names) or "").strip()


def priority_for(doc: Any) -> str:
    """LOW when a recommended item is missing; NORMAL otherwise."""
    dtype = _doc_type(doc)
    if not dtype or dtype in EXEMPT_TYPES:
        return NORMAL  # ponytail: exempt by type, reports never auto-lower
    if dtype == "transport":
        if _missing(doc, "receipt_path", "receipt"):
            return LOW
        if _missing(doc, "report_ref", "report_reference"):
            return LOW
        return NORMAL
    if dtype == "advance":
        return NORMAL if _text_ok(_val(doc, "reason")) else LOW
    if dtype in ("leave", "mission"):
        if not _text_ok(_val(doc, "reason")):
            return LOW
        if _missing(doc, "start_date", "start") or _missing(doc, "end_date", "end"):
            return LOW
        return NORMAL
    if dtype == "overtime":
        if _missing(doc, "start_date", "date", "day_date"):
            return LOW
        if not _val(doc, "hours"):
            return LOW
        return NORMAL if _text_ok(_val(doc, "reason", "text")) else LOW
    if dtype in ("correction", "note", "claim", "attendance_claim"):
        return NORMAL if _text_ok(_val(doc, "text")) else LOW
    if dtype in ("grievance", "complaint", "suggestion", "resignation",
                 "case"):
        return NORMAL if _text_ok(_val(doc, "summary", "text")) else LOW
    return NORMAL


def is_low(doc: Any) -> bool:
    """True when priority_for(doc) is LOW."""
    return priority_for(doc) == LOW


def sort_low_last(rows: list) -> list:
    """Stable: NORMAL first in original order, LOW last (never dropped)."""
    return sorted(rows, key=lambda r: 1 if is_low(r) else 0)
