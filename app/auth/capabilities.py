"""Capability registry: authority primitives for Atlas-Bot.

Roles are HR labels. WHAT a user may do WHERE is decided by
capabilities + scope. Scopes: SELF, SITE, MULTI, HQ, GLOBAL.
"""

from __future__ import annotations

SCOPES = ("SELF", "SITE", "MULTI", "HQ", "GLOBAL")

# name -> (default scope, description)
CAPABILITIES: dict[str, tuple[str, str]] = {
    # self-service (Flash)
    "view_own_attendance": ("SELF", "View own attendance"),
    "view_own_payroll": ("SELF", "View own payroll"),
    "view_own_requests": ("SELF", "View own HR requests"),
    "submit_leave": ("SELF", "File a leave request"),
    "submit_mission": ("SELF", "File a mission request"),
    "submit_advance": ("SELF", "File a salary advance"),
    "submit_grievance": ("SELF", "File a grievance"),
    "submit_complaint": ("SELF", "File a complaint"),
    "submit_suggestion": ("SELF", "Submit a suggestion"),
    "submit_resignation": ("SELF", "Submit resignation"),
    "check_in": ("SELF", "Check in"),
    "check_out": ("SELF", "Check out"),
    "request_overtime": ("SELF", "Request overtime"),
    # site operations
    "create_daily_report": ("SITE", "Create/edit draft reports"),
    "submit_hr_request": ("SELF", "File HR advance/transport requests"),
    "confirm_hr_request": ("SITE", "PM-confirm HR requests (gate 1)"),
    "delegate_hr_request": ("SITE", "Delegate HR requests to HQ HR"),
    "decide_hr_request": ("SITE", "HR-decide requests (gate 2, final)"),
    "approve_daily_report": ("SITE", "Approve daily reports"),
    "view_site_reports": ("SITE", "View site reports"),
    "manage_attendance": ("SITE", "Manage site attendance"),
    # HQ / cross-site
    "view_hq_reports": ("HQ", "View reports across sites"),
    "review_disciplinary_case": ("HQ", "Review disciplinary cases"),
    "approve_disciplinary_action": ("HQ", "Approve disciplinary actions"),
    "review_grievance": ("HQ", "Review grievances"),
    "resolve_grievance": ("HQ", "Resolve grievances"),
    "review_resignation": ("HQ", "Process resignations"),
    "manage_payroll": ("HQ", "Manage payroll"),
    "confirm_payout": ("SITE", "Confirm money payout"),
    "confirm_payroll_deduction": ("SITE", "Confirm payroll deduction"),
    # system
    "request_master_data_change": ("GLOBAL", "Request master-data change"),
    "approve_master_data_change": ("GLOBAL", "Approve master-data change"),
    "modify_master_data": ("GLOBAL", "Apply master-data change"),
}

# Role -> default capabilities (used when a user has no explicit grants).
# Explicit membership grants always win over these defaults.
ROLE_DEFAULTS: dict[str, frozenset] = {
    "superadmin": frozenset(CAPABILITIES),
    "project_manager": frozenset({
        "view_own_attendance", "view_own_payroll", "view_own_requests",
        "submit_leave", "submit_mission", "submit_advance",
        "submit_grievance", "submit_complaint", "submit_suggestion",
        "submit_resignation", "check_in", "check_out", "request_overtime",
        "create_daily_report", "approve_daily_report", "view_site_reports",
        "manage_attendance", "view_hq_reports",
        "confirm_payout", "confirm_payroll_deduction",
        "submit_hr_request", "confirm_hr_request", "delegate_hr_request",
    }),
    "executive_engineer": frozenset({
        "view_own_attendance", "view_own_payroll", "view_own_requests",
        "submit_leave", "submit_mission", "submit_advance",
        "submit_grievance", "submit_complaint", "submit_suggestion",
        "submit_resignation", "check_in", "check_out", "request_overtime",
        "create_daily_report", "approve_daily_report", "view_site_reports",
        "manage_attendance",
    }),
    "admin": frozenset({
        "view_own_attendance", "view_own_payroll", "view_own_requests",
        "submit_leave", "submit_mission", "submit_advance",
        "submit_grievance", "submit_complaint", "submit_suggestion",
        "submit_resignation", "check_in", "check_out", "request_overtime",
        "create_daily_report", "approve_daily_report", "view_site_reports",
        "manage_attendance",
    }),
    "hr": frozenset({
        "view_own_attendance", "view_own_payroll", "view_own_requests",
        "submit_leave", "submit_mission", "submit_advance",
        "submit_grievance", "submit_complaint", "submit_suggestion",
        "submit_resignation", "check_in", "check_out", "request_overtime",
        "view_site_reports", "view_hq_reports",
        "review_disciplinary_case", "review_grievance", "resolve_grievance",
        "review_resignation", "manage_payroll",
        "confirm_payout", "confirm_payroll_deduction",
        "submit_hr_request", "decide_hr_request",
    }),
    "normal_user": frozenset({
        "view_own_attendance", "view_own_payroll", "view_own_requests",
        "submit_leave", "submit_mission", "submit_advance",
        "submit_grievance", "submit_complaint", "submit_suggestion",
        "submit_resignation", "check_in", "check_out", "request_overtime",
        "create_daily_report", "submit_hr_request",
    }),
    "viewer": frozenset({
        "view_own_attendance", "view_own_payroll", "view_own_requests",
        "submit_hr_request",
    }),
    "pending": frozenset(),
    "rejected": frozenset(),
}


def is_known(capability: str) -> bool:
    """Check a capability name exists in the registry."""
    return capability in CAPABILITIES


def default_scope(capability: str) -> str:
    """Default scope for a capability (SELF if unknown)."""
    info = CAPABILITIES.get(capability)
    return info[0] if info else "SELF"


def for_role(role: str) -> frozenset:
    """Default capability set for a role (empty if unknown)."""
    return ROLE_DEFAULTS.get(role, frozenset())
