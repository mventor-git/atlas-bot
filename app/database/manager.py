"""
Database connection manager for Labor-Report.

Handles SQLite connection lifecycle, schema initialization,
and provides a context manager for safe database operations.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from app.database import driver
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)

try:
    import psycopg2

    _DB_ERRORS = (sqlite3.Error, psycopg2.Error)
except ImportError:  # Postgres driver optional; SQLite always works
    _DB_ERRORS = (sqlite3.Error,)

# Schema definition for the reports database (v2.0)
SCHEMA_SQL = """
-- Core reports table (v2.0: extended lifecycle fields)
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    day TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'final', 'locked', 'no_report')),
    pdf_path TEXT,
    excel_path TEXT,
    preview_pdf_path TEXT,
    telegram_user TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    finalized_at TEXT,
    locked_at TEXT,
    locked_by TEXT,
    source_date TEXT,
    UNIQUE(date, site_id)
);

-- Report items (contractor rows)
CREATE TABLE IF NOT EXISTS report_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    contractor TEXT NOT NULL,
    type TEXT,
    zone TEXT,
    workers INTEGER,
    details TEXT,
    contractor_code TEXT,
    craftsmen INTEGER,
    helpers INTEGER,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

-- Version history (v2.0)
CREATE TABLE IF NOT EXISTS report_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    change_summary TEXT,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    UNIQUE(report_id, version_number)
);

-- Event log / audit trail (v2.0)
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    telegram_user TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    action TEXT NOT NULL,
    object_type TEXT,
    object_id INTEGER,
    object_date TEXT,
    old_value TEXT,
    new_value TEXT
);

-- Per-user favorite contractors (v2.0)
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user TEXT NOT NULL,
    contractor_name TEXT NOT NULL,
    contractor_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(telegram_user, contractor_name)
);

-- Pre-computed statistics cache (v2.0)
CREATE TABLE IF NOT EXISTS stats_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    data TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    UNIQUE(period, period_key)
);

-- Indexes for reports
CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_date_status ON reports(date, status);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(telegram_user);

-- Indexes for report_items
CREATE INDEX IF NOT EXISTS idx_report_items_report_id ON report_items(report_id);
CREATE INDEX IF NOT EXISTS idx_report_items_contractor ON report_items(contractor);
CREATE INDEX IF NOT EXISTS idx_report_items_type ON report_items(type);
CREATE INDEX IF NOT EXISTS idx_report_items_zone ON report_items(zone);
CREATE INDEX IF NOT EXISTS idx_report_items_workers ON report_items(workers);

-- Indexes for versions
CREATE INDEX IF NOT EXISTS idx_versions_report_id ON report_versions(report_id);

-- Indexes for event_log
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON event_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_user ON event_log(telegram_user);
CREATE INDEX IF NOT EXISTS idx_events_action ON event_log(action);
CREATE INDEX IF NOT EXISTS idx_events_object ON event_log(object_type, object_id);

-- Indexes for favorites
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(telegram_user);

-- Indexes for stats_cache
CREATE INDEX IF NOT EXISTS idx_stats_period ON stats_cache(period, period_key);

-- Users / roles table (v3.0)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'pending'
        CHECK (role IN ('superadmin', 'project_manager', 'executive_engineer', 'admin', 'hr', 'normal_user', 'viewer', 'pending', 'rejected')),
    username TEXT,
    first_name TEXT,
    site_id TEXT NOT NULL DEFAULT 'default',
    monthly_salary REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_by TEXT,
    approved_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_site ON users(site_id);

-- User-added contractors (mventor-ticket-036)
CREATE TABLE IF NOT EXISTS contractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    type TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_contractors_name ON contractors(name);

-- HR requests (advance + transport + leave + mission + overtime chain)
CREATE TABLE IF NOT EXISTS hr_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_chat_id TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    request_type TEXT NOT NULL CHECK (request_type IN ('advance', 'transport', 'leave', 'mission', 'overtime')),
    amount REAL NOT NULL,
    reason TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    trip_date TEXT,
    report_ref TEXT,
    receipt_path TEXT,
    deduction_month TEXT,
    start_date TEXT,
    end_date TEXT,
    hours REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'pm_confirmed', 'approved', 'rejected')),
    assigned_to TEXT,
    delegated INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    signatures TEXT NOT NULL DEFAULT '[]',
    pdf_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hr_requests_site ON hr_requests(site_id);
CREATE INDEX IF NOT EXISTS idx_hr_requests_status ON hr_requests(status);
CREATE INDEX IF NOT EXISTS idx_hr_requests_requester ON hr_requests(requester_chat_id);

-- Site memberships (008 tenancy: user -> sites + capability grants)
CREATE TABLE IF NOT EXISTS user_site_memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    capabilities TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended')),
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, site_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_chat ON user_site_memberships(chat_id);
CREATE INDEX IF NOT EXISTS idx_memberships_site ON user_site_memberships(site_id);

-- Money events (009 ledger: append-only financial facts)
CREATE TABLE IF NOT EXISTS payout_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    amount REAL NOT NULL,
    payout_date TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    reference TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES hr_requests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS deduction_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    amount REAL NOT NULL,
    period TEXT NOT NULL,
    deduction_date TEXT,
    confirmed_by TEXT NOT NULL,
    reference TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES hr_requests(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payout_request ON payout_events(request_id);
CREATE INDEX IF NOT EXISTS idx_deduction_request ON deduction_events(request_id);
CREATE INDEX IF NOT EXISTS idx_deduction_period ON deduction_events(period);

-- Attendance events (013 evidence chain; never boolean)
CREATE TABLE IF NOT EXISTS attendance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    event_date TEXT NOT NULL,
    check_type TEXT NOT NULL DEFAULT 'in' CHECK (check_type IN ('in', 'out')),
    method TEXT NOT NULL DEFAULT 'self' CHECK (method IN ('self', 'assisted')),
    latitude REAL,
    longitude REAL,
    accuracy_m REAL,
    location_verdict TEXT,
    assisted_target_chat_id TEXT,
    assisted_reason TEXT,
    initiated_by TEXT,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'pending_verification', 'confirmed',
                          'disputed', 'exception', 'resolved')),
    verdict TEXT,
    confirmed_by TEXT,
    late_minutes INTEGER,
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_chat_date ON attendance_events(chat_id, event_date);
CREATE INDEX IF NOT EXISTS idx_attendance_site_date ON attendance_events(site_id, event_date);
CREATE INDEX IF NOT EXISTS idx_attendance_status ON attendance_events(status);

-- Case events (014 P6a; filed -> under_review -> resolved, filer appeal)
CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    case_type TEXT NOT NULL
        CHECK (case_type IN ('grievance', 'complaint', 'suggestion', 'resignation')),
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'filed'
        CHECK (status IN ('filed', 'under_review', 'resolved', 'appealed')),
    reviewed_by TEXT,
    resolved_by TEXT,
    resolution_note TEXT,
    appeal_note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_case_reporter ON case_events(reporter_chat_id, site_id);
CREATE INDEX IF NOT EXISTS idx_case_site_status ON case_events(site_id, status);

-- Discipline events (017 014c; filed -> under_review -> decided, subject appeal)
CREATE TABLE IF NOT EXISTS discipline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_chat_id TEXT NOT NULL,
    filed_by TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'filed'
        CHECK (status IN ('filed', 'under_review', 'decided', 'appealed')),
    reviewed_by TEXT,
    decided_by TEXT,
    decision TEXT,
    decision_note TEXT,
    appeal_note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_discipline_subject ON discipline_events(subject_chat_id, site_id);
CREATE INDEX IF NOT EXISTS idx_discipline_site_status ON discipline_events(site_id, status);

-- Payroll runs + lines (020 P5; draft editable, export locks)
CREATE TABLE IF NOT EXISTS payroll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL DEFAULT 'default',
    period TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'exported')),
    ot_multiplier REAL NOT NULL DEFAULT 1.5,
    standard_hours REAL NOT NULL DEFAULT 240.0,
    created_by TEXT,
    created_at TEXT NOT NULL,
    exported_at TEXT,
    UNIQUE (site_id, period)
);

CREATE TABLE IF NOT EXISTS payroll_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
    chat_id TEXT NOT NULL,
    base_pay REAL NOT NULL,
    ot_hours REAL NOT NULL DEFAULT 0,
    ot_amount REAL NOT NULL DEFAULT 0,
    advances REAL NOT NULL DEFAULT 0,
    deductions REAL NOT NULL DEFAULT 0,
    net REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payroll_run_period ON payroll_runs(site_id, period);
CREATE INDEX IF NOT EXISTS idx_payroll_line_run ON payroll_lines(run_id);

-- Attendance days (026 day model; events stay the evidence layer)
CREATE TABLE IF NOT EXISTS attendance_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    day_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'disputed', 'resolved')),
    origin TEXT NOT NULL DEFAULT 'none'
        CHECK (origin IN ('self', 'assisted', 'none')),
    first_in TEXT,
    last_out TEXT,
    late_minutes INTEGER,
    verdict TEXT,
    resolution_note TEXT,
    resolved_by TEXT,
    dispute_note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (site_id, chat_id, day_date)
);

CREATE TABLE IF NOT EXISTS attendance_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    day_date TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    kind TEXT NOT NULL CHECK (kind IN ('correction', 'note')),
    text TEXT NOT NULL,
    raised_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'approved', 'denied')),
    decided_by TEXT,
    decision_note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_day_site_date ON attendance_days(site_id, day_date);
CREATE INDEX IF NOT EXISTS idx_attendance_day_chat ON attendance_days(chat_id, site_id);
CREATE INDEX IF NOT EXISTS idx_attendance_claim_day ON attendance_claims(site_id, chat_id, day_date);
"""

# Postgres-native schema (v3.0): site-scoped tenants, hr role, now() defaults.
# Kept explicit (not generated) for auditability.
PG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    day TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'final', 'locked', 'no_report')),
    pdf_path TEXT,
    excel_path TEXT,
    preview_pdf_path TEXT,
    telegram_user TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    finalized_at TEXT,
    locked_at TEXT,
    locked_by TEXT,
    source_date TEXT,
    UNIQUE(date, site_id)
);

CREATE TABLE IF NOT EXISTS report_items (
    id SERIAL PRIMARY KEY,
    report_id INTEGER NOT NULL,
    contractor TEXT NOT NULL,
    type TEXT,
    zone TEXT,
    workers INTEGER,
    details TEXT,
    contractor_code TEXT,
    craftsmen INTEGER,
    helpers INTEGER,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS report_versions (
    id SERIAL PRIMARY KEY,
    report_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    change_summary TEXT,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    UNIQUE(report_id, version_number)
);

CREATE TABLE IF NOT EXISTS event_log (
    id SERIAL PRIMARY KEY,
    timestamp TEXT NOT NULL,
    telegram_user TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    action TEXT NOT NULL,
    object_type TEXT,
    object_id INTEGER,
    object_date TEXT,
    old_value TEXT,
    new_value TEXT
);

CREATE TABLE IF NOT EXISTS favorites (
    id SERIAL PRIMARY KEY,
    telegram_user TEXT NOT NULL,
    contractor_name TEXT NOT NULL,
    contractor_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(telegram_user, contractor_name)
);

CREATE TABLE IF NOT EXISTS stats_cache (
    id SERIAL PRIMARY KEY,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    data TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    UNIQUE(period, period_key)
);

CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_site ON reports(site_id);
CREATE INDEX IF NOT EXISTS idx_reports_date_status ON reports(date, status);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(telegram_user);

CREATE INDEX IF NOT EXISTS idx_report_items_report_id ON report_items(report_id);
CREATE INDEX IF NOT EXISTS idx_report_items_contractor ON report_items(contractor);
CREATE INDEX IF NOT EXISTS idx_report_items_type ON report_items(type);
CREATE INDEX IF NOT EXISTS idx_report_items_zone ON report_items(zone);
CREATE INDEX IF NOT EXISTS idx_report_items_workers ON report_items(workers);

CREATE INDEX IF NOT EXISTS idx_versions_report_id ON report_versions(report_id);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON event_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_user ON event_log(telegram_user);
CREATE INDEX IF NOT EXISTS idx_events_site ON event_log(site_id);
CREATE INDEX IF NOT EXISTS idx_events_action ON event_log(action);
CREATE INDEX IF NOT EXISTS idx_events_object ON event_log(object_type, object_id);

CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(telegram_user);

CREATE INDEX IF NOT EXISTS idx_stats_period ON stats_cache(period, period_key);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'pending'
        CHECK (role IN ('superadmin', 'project_manager', 'executive_engineer', 'admin', 'hr', 'normal_user', 'viewer', 'pending', 'rejected')),
    username TEXT,
    first_name TEXT,
    site_id TEXT NOT NULL DEFAULT 'default',
    monthly_salary REAL,
    created_at TEXT NOT NULL DEFAULT (now()),
    approved_by TEXT,
    approved_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (now())
);

CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_site ON users(site_id);

CREATE TABLE IF NOT EXISTS contractors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (now())
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_contractors_name_unique ON contractors(LOWER(name));

CREATE INDEX IF NOT EXISTS idx_contractors_name ON contractors(name);

CREATE TABLE IF NOT EXISTS hr_requests (
    id SERIAL PRIMARY KEY,
    requester_chat_id TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    request_type TEXT NOT NULL CHECK (request_type IN ('advance', 'transport', 'leave', 'mission', 'overtime')),
    amount REAL NOT NULL,
    reason TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    trip_date TEXT,
    report_ref TEXT,
    receipt_path TEXT,
    deduction_month TEXT,
    start_date TEXT,
    end_date TEXT,
    hours REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'pm_confirmed', 'approved', 'rejected')),
    assigned_to TEXT,
    delegated INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    signatures TEXT NOT NULL DEFAULT '[]',
    pdf_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hr_requests_site ON hr_requests(site_id);
CREATE INDEX IF NOT EXISTS idx_hr_requests_status ON hr_requests(status);
CREATE INDEX IF NOT EXISTS idx_hr_requests_requester ON hr_requests(requester_chat_id);

CREATE TABLE IF NOT EXISTS user_site_memberships (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    capabilities TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended')),
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, site_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_chat ON user_site_memberships(chat_id);
CREATE INDEX IF NOT EXISTS idx_memberships_site ON user_site_memberships(site_id);

CREATE TABLE IF NOT EXISTS payout_events (
    id SERIAL PRIMARY KEY,
    request_id INTEGER NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    amount REAL NOT NULL,
    payout_date TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    reference TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES hr_requests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS deduction_events (
    id SERIAL PRIMARY KEY,
    request_id INTEGER NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    amount REAL NOT NULL,
    period TEXT NOT NULL,
    deduction_date TEXT,
    confirmed_by TEXT NOT NULL,
    reference TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES hr_requests(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payout_request ON payout_events(request_id);
CREATE INDEX IF NOT EXISTS idx_deduction_request ON deduction_events(request_id);
CREATE INDEX IF NOT EXISTS idx_deduction_period ON deduction_events(period);

CREATE TABLE IF NOT EXISTS attendance_events (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    event_date TEXT NOT NULL,
    check_type TEXT NOT NULL DEFAULT 'in' CHECK (check_type IN ('in', 'out')),
    method TEXT NOT NULL DEFAULT 'self' CHECK (method IN ('self', 'assisted')),
    latitude REAL,
    longitude REAL,
    accuracy_m REAL,
    location_verdict TEXT,
    assisted_target_chat_id TEXT,
    assisted_reason TEXT,
    initiated_by TEXT,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'pending_verification', 'confirmed',
                          'disputed', 'exception', 'resolved')),
    verdict TEXT,
    confirmed_by TEXT,
    late_minutes INTEGER,
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_chat_date ON attendance_events(chat_id, event_date);
CREATE INDEX IF NOT EXISTS idx_attendance_site_date ON attendance_events(site_id, event_date);
CREATE INDEX IF NOT EXISTS idx_attendance_status ON attendance_events(status);

CREATE TABLE IF NOT EXISTS case_events (
    id SERIAL PRIMARY KEY,
    reporter_chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    case_type TEXT NOT NULL
        CHECK (case_type IN ('grievance', 'complaint', 'suggestion', 'resignation')),
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'filed'
        CHECK (status IN ('filed', 'under_review', 'resolved', 'appealed')),
    reviewed_by TEXT,
    resolved_by TEXT,
    resolution_note TEXT,
    appeal_note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_case_reporter ON case_events(reporter_chat_id, site_id);
CREATE INDEX IF NOT EXISTS idx_case_site_status ON case_events(site_id, status);

CREATE TABLE IF NOT EXISTS discipline_events (
    id SERIAL PRIMARY KEY,
    subject_chat_id TEXT NOT NULL,
    filed_by TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'filed'
        CHECK (status IN ('filed', 'under_review', 'decided', 'appealed')),
    reviewed_by TEXT,
    decided_by TEXT,
    decision TEXT,
    decision_note TEXT,
    appeal_note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_discipline_subject ON discipline_events(subject_chat_id, site_id);
CREATE INDEX IF NOT EXISTS idx_discipline_site_status ON discipline_events(site_id, status);

CREATE TABLE IF NOT EXISTS payroll_runs (
    id SERIAL PRIMARY KEY,
    site_id TEXT NOT NULL DEFAULT 'default',
    period TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'exported')),
    ot_multiplier REAL NOT NULL DEFAULT 1.5,
    standard_hours REAL NOT NULL DEFAULT 240.0,
    created_by TEXT,
    created_at TEXT NOT NULL,
    exported_at TEXT,
    UNIQUE (site_id, period)
);

CREATE TABLE IF NOT EXISTS payroll_lines (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES payroll_runs(id),
    chat_id TEXT NOT NULL,
    base_pay REAL NOT NULL,
    ot_hours REAL NOT NULL DEFAULT 0,
    ot_amount REAL NOT NULL DEFAULT 0,
    advances REAL NOT NULL DEFAULT 0,
    deductions REAL NOT NULL DEFAULT 0,
    net REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payroll_run_period ON payroll_runs(site_id, period);
CREATE INDEX IF NOT EXISTS idx_payroll_line_run ON payroll_lines(run_id);

CREATE TABLE IF NOT EXISTS attendance_days (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    day_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'disputed', 'resolved')),
    origin TEXT NOT NULL DEFAULT 'none'
        CHECK (origin IN ('self', 'assisted', 'none')),
    first_in TEXT,
    last_out TEXT,
    late_minutes INTEGER,
    verdict TEXT,
    resolution_note TEXT,
    resolved_by TEXT,
    dispute_note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (site_id, chat_id, day_date)
);

CREATE TABLE IF NOT EXISTS attendance_claims (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL,
    day_date TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'default',
    kind TEXT NOT NULL CHECK (kind IN ('correction', 'note')),
    text TEXT NOT NULL,
    raised_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'approved', 'denied')),
    decided_by TEXT,
    decision_note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_day_site_date ON attendance_days(site_id, day_date);
CREATE INDEX IF NOT EXISTS idx_attendance_day_chat ON attendance_days(chat_id, site_id);
CREATE INDEX IF NOT EXISTS idx_attendance_claim_day ON attendance_claims(site_id, chat_id, day_date);
"""


class DatabaseManager:
    """Manages SQLite database connection and schema.

    Thread-safe connection handling with automatic schema initialization.
    Designed for single-user/small-team usage with SQLite.

    Usage:
        db = DatabaseManager("database/labor_reports.db")
        with db.connection() as conn:
            cursor = conn.execute("SELECT * FROM reports")
            ...
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the database manager.

        Backend: SQLite file by default; Postgres when the DATABASE_URL
        environment variable is set. Repositories keep ``?`` placeholders
        on both backends (translated at execution).

        Args:
            db_path: Path to the SQLite database file (ignored on Postgres,
                kept for API compatibility).

        Raises:
            DatabaseError: If the database directory cannot be created
                           or schema initialization fails.
        """
        self._db_path = Path(db_path).resolve()
        self._local = threading.local()
        self._lock = threading.Lock()
        self._pg = driver.use_postgres()

        if not self._pg:
            # Ensure parent directory exists (SQLite only)
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise DatabaseError(
                    f"Cannot create database directory: {self._db_path.parent}",
                    original_exception=e,
                ) from e

        # Initialize schema on startup (creates new tables/indexes)
        self._init_schema()

        if not self._pg:
            # Run v1.0 → v2.0 migration if needed (adds columns, migrates data)
            self.run_migration()

            # Widen users.role CHECK to include extended roles on existing DBs
            # (CREATE TABLE IF NOT EXISTS never alters an existing table).
            self._ensure_extended_roles()

        # Tenant scoping columns on both backends (idempotent).
        self.ensure_site_columns()
        # HR table widening for leave/mission/overtime (idempotent).
        self._ensure_hr_requests_v2()
        # Salary column on pre-existing users tables (idempotent).
        self._ensure_users_salary()
        # Craftsman/helper split on pre-existing report_items (idempotent).
        self._ensure_report_items_split()
        # One-report-per-day-PER-SITE uniqueness on pre-existing DBs.
        self._ensure_site_uniques()

        logger.info(
            "Database initialized: %s (backend=%s)",
            self._db_path if not self._pg else "postgres",
            "postgres" if self._pg else "sqlite",
        )

    @property
    def db_path(self) -> Path:
        """Get the resolved database file path."""
        return self._db_path

    def _get_connection(self):
        """Get a thread-local database connection.

        Creates a new connection if one doesn't exist for this thread.

        Returns:
            An active connection (SQLite with WAL + FK, or Postgres).
        """
        if not hasattr(self._local, "connection") or self._local.connection is None:
            if self._pg:
                try:
                    import psycopg2
                    from psycopg2.extras import RealDictCursor
                except ImportError as e:
                    raise DatabaseError(
                        "psycopg2 is required for Postgres mode. "
                        "Install it with: pip install psycopg2-binary",
                        original_exception=e,
                    ) from e
                conn = psycopg2.connect(driver.database_url(), cursor_factory=RealDictCursor)
                conn.autocommit = False
            else:
                conn = sqlite3.connect(str(self._db_path))
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.row_factory = sqlite3.Row
            self._local.connection = conn
        return self._local.connection

    def connection(self) -> sqlite3.Connection:
        """Get a database connection for the current thread.

        Returns:
            An active SQLite connection with WAL mode and foreign keys enabled.
        """
        return self._get_connection()

    def _init_schema(self) -> None:
        """Initialize the database schema.

        Creates tables and indexes if they don't exist.

        Raises:
            DatabaseError: If schema initialization fails.
        """
        try:
            if self._pg:
                self._exec_statements(PG_SCHEMA_SQL)
            else:
                conn = self._get_connection()
                conn.executescript(SCHEMA_SQL)
                conn.commit()
            logger.debug("Database schema initialized successfully.")
        except _DB_ERRORS as e:
            raise DatabaseError(
                f"Failed to initialize database schema: {e}",
                original_exception=e,
            ) from e

    def _exec_statements(self, sql: str) -> None:
        """Execute multi-statement SQL (statement splitter for Postgres)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        for statement in sql.split(";"):
            stmt = statement.strip()
            if stmt:
                cursor.execute(driver.translate(stmt, self._pg))
        conn.commit()

    def _ensure_users_salary(self) -> None:
        """Add users.monthly_salary on old DBs (idempotent, both backends)."""
        if not self.table_exists("users"):
            return
        if self.column_exists("users", "monthly_salary"):
            return
        self.execute("ALTER TABLE users ADD COLUMN monthly_salary REAL")
        self.commit()
        logger.info("Added monthly_salary to users.")

    def _ensure_report_items_split(self) -> None:
        """Add report_items.craftsmen/helpers on old DBs (idempotent)."""
        if not self.table_exists("report_items"):
            return
        for column in ("craftsmen", "helpers"):
            if not self.column_exists("report_items", column):
                self.execute(f"ALTER TABLE report_items ADD COLUMN {column} INTEGER")
                self.commit()
                logger.info("Added %s to report_items.", column)

    def ensure_site_columns(self) -> None:
        """Add tenant site_id columns to existing tables (idempotent).

        Covers manager-owned tables plus repository-owned ones
        (user_activity_log, recent_contractors carry site_id in their
        own DDL and call this after rebuilds).
        """
        for table in ("reports", "event_log", "user_activity_log", "recent_contractors", "users"):
            if not self.table_exists(table):
                continue  # repository-owned tables are created lazily by repos
            if not self.column_exists(table, "site_id"):
                self.execute(
                    f"ALTER TABLE {table} ADD COLUMN site_id TEXT NOT NULL DEFAULT 'default'"
                )
                self.commit()
                logger.info("Added site_id to %s.", table)

    def _ensure_hr_requests_v2(self) -> None:
        """Widen hr_requests for leave/mission/overtime on old DBs.

        Rebuilds (rename → create → copy → drop) when the new columns
        or types are missing. FK enforcement stays ON: pg RENAME keeps
        OID references intact; SQLite path disables FK during the swap
        (DROP TABLE would otherwise cascade child money rows).
        Idempotent: no-op when start_date exists with the v2 CHECK.
        """
        if not self.table_exists("hr_requests"):
            return  # fresh installs get v2 DDL directly
        if self.column_exists("hr_requests", "start_date"):
            return
        cols = ("id, requester_chat_id, requester_name, request_type,"
                " amount, reason, site_id, trip_date, report_ref,"
                " receipt_path, deduction_month, status, assigned_to,"
                " delegated, note, signatures, pdf_path, created_at,"
                " updated_at")
        try:
            conn = self._get_connection()
            if not self._pg:
                conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("ALTER TABLE hr_requests RENAME TO hr_requests_old")
            conn.execute(
                """CREATE TABLE hr_requests (
                    id %s,
                    requester_chat_id TEXT NOT NULL,
                    requester_name TEXT NOT NULL,
                    request_type TEXT NOT NULL CHECK (request_type IN
                        ('advance', 'transport', 'leave', 'mission', 'overtime')),
                    amount REAL NOT NULL,
                    reason TEXT NOT NULL,
                    site_id TEXT NOT NULL DEFAULT 'default',
                    trip_date TEXT,
                    report_ref TEXT,
                    receipt_path TEXT,
                    deduction_month TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    hours REAL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'pm_confirmed', 'approved', 'rejected')),
                    assigned_to TEXT,
                    delegated INTEGER NOT NULL DEFAULT 0,
                    note TEXT,
                    signatures TEXT NOT NULL DEFAULT '[]',
                    pdf_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )""" % ("SERIAL PRIMARY KEY" if self._pg else "INTEGER PRIMARY KEY AUTOINCREMENT")
            )
            conn.execute(
                "INSERT INTO hr_requests (%s) SELECT %s FROM hr_requests_old" % (cols, cols)
            )
            conn.execute("DROP TABLE hr_requests_old")
            if not self._pg:
                conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            logger.info("hr_requests widened to v2 (leave/mission/overtime).")
        except _DB_ERRORS as e:
            try:
                if not self._pg:
                    conn.execute("PRAGMA foreign_keys=ON")
            except _DB_ERRORS:
                pass
            conn.rollback()
            raise DatabaseError(
                f"Failed to widen hr_requests: {e}",
                original_exception=e,
            ) from e

    def _ensure_site_uniques(self) -> None:
        """Replace global UNIQUE(date) with UNIQUE(date, site_id) on old DBs.

        SQLite-only rebuild (rename → create → copy → drop). No-op on
        fresh databases and on Postgres (schema already composite).
        """
        if self._pg:
            return
        row = self.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='reports'"
        ).fetchone()
        ddl = row["sql"] or "" if row else ""
        if row is None or "UNIQUE(date, site_id)" in ddl:
            return
        try:
            conn = self._get_connection()
            # Disable FK enforcement during the rename: otherwise SQLite
            # rewrites report_items' FK to reports_old and DROP TABLE
            # cascades the child rows away.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("ALTER TABLE reports RENAME TO reports_old")
            conn.execute(
                """CREATE TABLE reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    day TEXT NOT NULL,
                    site_id TEXT NOT NULL DEFAULT 'default',
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'final', 'locked', 'no_report')),
                    pdf_path TEXT,
                    excel_path TEXT,
                    preview_pdf_path TEXT,
                    telegram_user TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    finalized_at TEXT,
                    locked_at TEXT,
                    locked_by TEXT,
                    source_date TEXT,
                    UNIQUE(date, site_id)
                )"""
            )
            conn.execute(
                """INSERT INTO reports
                    (id, date, day, site_id, status, pdf_path, excel_path,
                     preview_pdf_path, telegram_user, created_at, updated_at,
                     finalized_at, locked_at, locked_by, source_date)
                    SELECT id, date, day,
                     COALESCE(site_id, 'default'), status, pdf_path, excel_path,
                     preview_pdf_path, telegram_user, created_at, updated_at,
                     finalized_at, locked_at, locked_by, source_date
                    FROM reports_old"""
            )
            conn.execute("DROP TABLE reports_old")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_site ON reports(site_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_date_status ON reports(date, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(telegram_user)")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            logger.info("reports uniqueness widened to (date, site_id).")
        except _DB_ERRORS as e:
            try:
                conn.execute("PRAGMA foreign_keys=ON")
            except _DB_ERRORS:
                pass
            conn.rollback()
            raise DatabaseError(
                f"Failed to widen reports uniqueness: {e}",
                original_exception=e,
            ) from e

    def execute(self, sql: str, params: tuple = ()):
        """Execute a SQL statement.

        ``?`` placeholders are translated for Postgres automatically.
        On Postgres, plain INSERTs gain ``RETURNING id`` so
        ``cursor.lastrowid`` keeps working.

        Args:
            sql: SQL statement to execute.
            params: Parameters for the SQL statement.

        Returns:
            The cursor after execution.

        Raises:
            DatabaseError: If execution fails.
        """
        try:
            conn = self._get_connection()
            sql = driver.translate(sql, self._pg)
            if self._pg:
                sql = driver.ddl_fixups(sql)
            returning = driver.needs_returning(sql, self._pg)
            if returning:
                sql = sql + " RETURNING id"
            if self._pg:
                cursor = conn.cursor()
                cursor.execute(sql, params)
            else:
                cursor = conn.execute(sql, params)
            if returning:
                return driver.PgCursor(cursor)
            return cursor
        except _DB_ERRORS as e:
            raise DatabaseError(f"Database execute error: {e}", original_exception=e) from e

    def executemany(self, sql: str, params_list: list[tuple]):
        """Execute a SQL statement multiple times with different parameters.

        Args:
            sql: SQL statement to execute.
            params_list: List of parameter tuples.

        Returns:
            The cursor after execution.

        Raises:
            DatabaseError: If execution fails.
        """
        try:
            conn = self._get_connection()
            sql = driver.translate(sql, self._pg)
            if self._pg:
                cursor = conn.cursor()
                cursor.executemany(sql, params_list)
                return cursor
            return conn.executemany(sql, params_list)
        except _DB_ERRORS as e:
            raise DatabaseError(
                f"Database executemany error: {e}", original_exception=e
            ) from e

    def commit(self) -> None:
        """Commit the current transaction.

        Raises:
            DatabaseError: If commit fails.
        """
        try:
            self._get_connection().commit()
        except _DB_ERRORS as e:
            raise DatabaseError(f"Database commit error: {e}", original_exception=e) from e

    def rollback(self) -> None:
        """Roll back the current transaction.

        Raises:
            DatabaseError: If rollback fails.
        """
        try:
            self._get_connection().rollback()
        except _DB_ERRORS as e:
            raise DatabaseError(
                f"Database rollback error: {e}", original_exception=e
            ) from e

    def close(self) -> None:
        """Close the database connection for the current thread.

        Performs a WAL checkpoint and switches to DELETE journal mode
        before closing to ensure the WAL and SHM files are cleaned up,
        preventing PermissionErrors on Windows during temp directory cleanup.
        """
        if hasattr(self._local, "connection") and self._local.connection is not None:
            if not self._pg:
                try:
                    # Checkpoint WAL and switch to DELETE mode so -wal and -shm
                    # files are removed when the connection closes.
                    self._local.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    self._local.connection.execute("PRAGMA journal_mode=DELETE")
                    self._local.connection.commit()
                except _DB_ERRORS:
                    pass  # Best-effort cleanup
            try:
                self._local.connection.close()
            except _DB_ERRORS as e:
                logger.warning("Error closing database connection: %s", e)
            finally:
                self._local.connection = None

    def close_all(self) -> None:
        """Close all database connections (thread-safe).

        Should be called during application shutdown.
        """
        # The thread-local connections will be garbage collected
        # when the thread ends. This is a best-effort cleanup.
        self.close()

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database.

        Args:
            table_name: Name of the table to check.

        Returns:
            True if the table exists.
        """
        if self._pg:
            cursor = self.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name=?",
                (table_name,),
            )
        else:
            cursor = self.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
        return cursor.fetchone() is not None

    def column_exists(self, table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table.

        Args:
            table_name: Name of the table.
            column_name: Name of the column to check.

        Returns:
            True if the column exists.
        """
        if self._pg:
            cursor = self.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=? AND column_name=?",
                (table_name, column_name),
            )
            return cursor.fetchone() is not None
        cursor = self.execute(f"PRAGMA table_info({table_name})")
        columns = [row["name"] for row in cursor.fetchall()]
        return column_name in columns

    # --- v2.0 Migration ---

    def run_migration(self) -> bool:
        """Run v1.0 to v2.0 database migration if needed.

        Checks if the database needs migration (v1.0 schema detected)
        and runs the migration to add all v2.0 tables, columns, and indexes.

        The migration is idempotent — safe to run multiple times.

        Returns:
            True if migration was performed, False if already up-to-date.

        Raises:
            DatabaseError: If migration fails.
        """
        if not self._needs_migration():
            logger.info("Database schema is already v2.0 — no migration needed.")
            return False

        logger.info("v1.0 database detected — starting migration to v2.0...")
        self._migrate_v1_to_v2()
        logger.info("Database migration to v2.0 completed successfully.")
        return True

    def _needs_migration(self) -> bool:
        """Check if the database needs migration from v1.0 to v2.0.

        Detects v1.0 schema by checking for the absence of v2.0 columns
        (e.g., 'updated_at' in the reports table).

        Returns:
            True if migration is needed.
        """
        if not self.table_exists("reports"):
            return False  # Fresh database, schema init handles it
        # Check for v2.0 column — if absent, it's a v1.0 database
        return not self.column_exists("reports", "updated_at")

    def _migrate_v1_to_v2(self) -> None:
        """Perform the v1.0 to v2.0 schema migration.

        Adds new columns to existing tables, creates new tables,
        adds indexes, and migrates status values.

        Uses ALTER TABLE for new columns (SQLite supports ADD COLUMN).
        Uses CREATE TABLE IF NOT EXISTS for new tables.
        All operations are wrapped in a transaction for atomicity.
        """
        try:
            conn = self._get_connection()

            # Step 1: Add new columns to reports table
            v2_report_columns = [
                ("preview_pdf_path", "TEXT"),
                ("updated_at", "TEXT"),
                ("finalized_at", "TEXT"),
                ("locked_at", "TEXT"),
                ("locked_by", "TEXT"),
                ("source_date", "TEXT"),
            ]
            for col_name, col_type in v2_report_columns:
                if not self.column_exists("reports", col_name):
                    conn.execute(
                        f"ALTER TABLE reports ADD COLUMN {col_name} {col_type}"
                    )
                    logger.debug("Added column 'reports.%s'", col_name)

            # Step 2: Add new column to report_items
            if not self.column_exists("report_items", "contractor_code"):
                conn.execute("ALTER TABLE report_items ADD COLUMN contractor_code TEXT")
                logger.debug("Added column 'report_items.contractor_code'")

            # Step 3: Migrate status values (generated → final)
            # The v1 schema has CHECK (status IN ('generated', 'no_report')),
            # so temporarily ignore CHECK constraints to allow the update.
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute(
                "UPDATE reports SET status = 'final' WHERE status = 'generated'"
            )
            conn.execute("PRAGMA ignore_check_constraints = OFF")
            migrated = conn.execute(
                "SELECT changes()"
            ).fetchone()[0]
            if migrated:
                logger.info("Migrated %d report status(es) from 'generated' to 'final'", migrated)

            # Step 4: Set updated_at for existing reports
            conn.execute(
                "UPDATE reports SET updated_at = created_at WHERE updated_at IS NULL"
            )
            logger.debug("Set updated_at for existing reports")

            # Step 5: Reinitialize schema (creates new tables and indexes)
            conn.executescript(SCHEMA_SQL)

            conn.commit()
            logger.info("v1.0 → v2.0 migration complete.")

        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(
                f"Database migration failed: {e}",
                original_exception=e,
            ) from e

    def _ensure_extended_roles(self) -> bool:
        """Widen the users.role CHECK to include extended roles.

        Covers 'project_manager', 'executive_engineer' and 'hr'. SQLite cannot
        ALTER a CHECK constraint, so an existing users table is rebuilt
        (rename → create → copy → drop). Idempotent: no-op when the
        CHECK already allows all extended roles.

        Returns:
            True if the table was rebuilt, False if already up-to-date.
        """
        row = self.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        ddl = row["sql"] or "" if row else ""
        if row is None or ("hr" in ddl and "project_manager" in ddl and "executive_engineer" in ddl):
            return False
        if self._pg:
            return False  # Postgres schema already includes hr
        try:
            conn = self._get_connection()
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("ALTER TABLE users RENAME TO users_old")
            conn.execute(
                """CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'pending'
                        CHECK (role IN ('superadmin', 'project_manager', 'executive_engineer', 'admin', 'hr', 'normal_user', 'viewer', 'pending', 'rejected')),
                    username TEXT,
                    first_name TEXT,
                    site_id TEXT NOT NULL DEFAULT 'default',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    approved_by TEXT,
                    approved_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            conn.execute(
                """INSERT INTO users
                    (id, chat_id, role, username, first_name, site_id, created_at,
                     approved_by, approved_at, updated_at)
                    SELECT id, chat_id, role, username, first_name,
                     COALESCE(site_id, 'default'), created_at,
                     approved_by, approved_at, updated_at FROM users_old"""
            )
            conn.execute("DROP TABLE users_old")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_site ON users(site_id)")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            logger.info("users.role CHECK widened to include extended roles.")
            return True
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(
                f"Failed to add extended roles: {e}",
                original_exception=e,
            ) from e
