"""Payroll domain models (ticket-020, P5).

Monthly run per ADR-009: gross = base + overtime - advances/deductions.
A run is editable until export; export locks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class PayrollRunStatus:
    DRAFT = "draft"
    EXPORTED = "exported"


@dataclass
class PayrollRun:
    """One monthly payroll run for a site."""

    period: str
    """YYYY-MM."""

    site_id: Optional[str] = None
    status: str = PayrollRunStatus.DRAFT
    ot_multiplier: float = 1.5
    """Dev-configurable overtime multiplier (owner default 1.5x)."""
    standard_hours: float = 240.0
    """Monthly hours divisor for the OT hourly rate (dev default)."""
    created_by: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    exported_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class PayrollLine:
    """One employee line inside a run (amounts rounded to 2dp)."""

    run_id: int
    chat_id: str
    base_pay: float
    ot_hours: float = 0.0
    ot_amount: float = 0.0
    advances: float = 0.0
    deductions: float = 0.0
    net: float = 0.0
    id: Optional[int] = None
    salary_history_id: Optional[int] = None
    """Provenance: salary_history row in effect when built (5A)."""
