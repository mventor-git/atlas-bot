"""Payroll domain models (ticket-020, P5; 5B policy).

Monthly run per ADR-009: gross = base + overtime - advances/deductions.
A run is editable until export; export locks it.
Calculation policy is versioned/effective-dated and snapshotted per run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class PayrollRunStatus:
    DRAFT = "draft"
    EXPORTED = "exported"


class HoursBasis:
    FIXED = "fixed"
    """Fixed monthly hours divisor (only implemented mode; 5B)."""


class RoundingRule:
    STANDARD_2DP = "standard_2dp"
    """round(x, 2) at OT and net steps = current behavior (only mode; 5B)."""


SYSTEM_POLICY_DEFAULTS = {
    "hours_basis": HoursBasis.FIXED,
    "standard_hours": 240.0,
    "ot_multiplier": 1.5,
    "rounding": RoundingRule.STANDARD_2DP,
}
"""Pre-policy fallback = current behavior, labeled origin system_default."""


@dataclass
class PayrollPolicy:
    """One versioned payroll calculation policy for a site (5B)."""

    site_id: str
    version: int
    effective_from: str
    """YYYY-MM-DD; applies to periods starting on/after this date."""
    hours_basis: str = HoursBasis.FIXED
    standard_hours: float = 240.0
    ot_multiplier: float = 1.5
    rounding: str = RoundingRule.STANDARD_2DP
    set_by: Optional[str] = None
    set_at: str = field(default_factory=lambda: datetime.now().isoformat())
    reason: Optional[str] = None
    id: Optional[int] = None


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
    policy_version: Optional[int] = None
    """Policy version frozen at creation (NULL = pre-policy era)."""
    policy_snapshot: Optional[str] = None
    """JSON of the exact params used (5B reproducibility)."""


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
    ot_source: str = "manual"
    """'manual' (provisional) or 'approved_requests' (5B provenance)."""
    ot_refs: Optional[str] = None
    """CSV of OT hr_request ids when approved (else NULL)."""
    advances_source: str = "manual"
    advances_refs: Optional[str] = None
    """CSV of deduction_event ids backing advances (else NULL)."""
    deductions_source: str = "manual"
    deductions_refs: Optional[str] = None
    """CSV of deduction_event ids backing deductions (else NULL)."""


@dataclass
class PayrollAdjustment:
    """Audited correction on an EXPORTED run (5B; locked rows never UPDATE)."""

    run_id: int
    chat_id: str
    amount: float
    reason: str
    created_by: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    id: Optional[int] = None
