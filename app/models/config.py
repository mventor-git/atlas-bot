"""
Configuration models for Labor-Report.

Uses Pydantic BaseModel for validation and type safety.
All configuration values are loaded from config.yaml.
"""

from datetime import time
from pathlib import Path
from typing import Optional

import logging
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class LoggingConfig(BaseModel):
    """Logging configuration."""

    file: str = Field("logs/app.log", description="Path to log file")
    level: str = Field("INFO", description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    max_bytes: int = Field(10_485_760, description="Maximum log file size before rotation", ge=1024)
    backup_count: int = Field(5, description="Number of backup log files to keep", ge=0)

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return upper


class OutputConfig(BaseModel):
    """Output path configuration for generated files."""

    pdf_folder: str = Field("exports/pdf", description="Folder for generated PDF files")
    docs_folder: str = Field("exports/docs", description="Folder for generated document files")
    preview_folder: str = Field("exports/preview", description="Folder for preview PDF files")
    pdf_database: str = Field("exports/pdf_database", description="Centralized PDF archive directory")


class TableColumnConfig(BaseModel):
    """Column mapping for the labor table in the document template.

    NOTE: Column A is unusable in the template, so serial starts at B.
    Columns map to the tblReport headers:
      B=Ù… (serial), C=Ø§Ø³Ù… Ø§Ù„Ù…Ù‚Ø§ÙˆÙ„ (contractor), D=Ø§Ù„Ø¨Ù†Ø¯ (type),
      E=Ù…ÙƒØ§Ù† Ø§Ù„Ø¹Ù…Ù„ (zone), F=Ø¹Ø¯Ø¯ Ø§Ù„Ø¹Ù…Ø§Ù„ (workers), G=Ø§Ù„Ø¹Ø¯Ø¯ Ø§Ù„ØªÙØµÙŠÙ„ÙŠ (details)
    """

    serial: str = Field("B", description="Column B = 'Ù…' (serial number â€” A is unusable)")
    contractor: str = Field("C", description="Column C = 'Ø§Ø³Ù… Ø§Ù„Ù…Ù‚Ø§ÙˆÙ„' (contractor name)")
    type: str = Field("D", description="Column D = 'Ø§Ù„Ø¨Ù†Ø¯' (contractor type/item)")
    zone: str = Field("E", description="Column E = 'Ù…ÙƒØ§Ù† Ø§Ù„Ø¹Ù…Ù„' (work zone)")
    workers: str = Field("F", description="Column F = 'Ø¹Ø¯Ø¯ Ø§Ù„Ø¹Ù…Ø§Ù„' (workers count)")
    details: str = Field("G", description="Column G = 'Ø§Ù„Ø¹Ø¯Ø¯ Ø§Ù„ØªÙØµÙŠÙ„ÙŠ' (detailed count)")


class TableConfig(BaseModel):
    """Configuration for the labor table in the template."""

    start_row: int = Field(11, description="First data row of the labor table (row 10 is header)", ge=1)
    columns: TableColumnConfig = Field(default_factory=TableColumnConfig)


class TemplateThresholdsConfig(BaseModel):
    """Thresholds for auto-selecting the appropriate template size."""

    medium: int = Field(7, description="Switch to medium template when rows exceed this", ge=1)
    large: int = Field(20, description="Switch to large template when rows exceed this", ge=1)


class TemplateConfig(BaseModel):
    """Configuration for the document template file and its cell mappings.

    Multi-template system auto-selects the appropriate template based on
    the number of data rows:
    - small_template: up to ``row_thresholds.medium`` rows
    - medium_template: up to ``row_thresholds.large`` rows
    - large_template: beyond ``row_thresholds.large`` rows
    """

    file: str = Field("templates/contractor-daily-labor-template.ods", description="Path to the document template (fallback)")
    tables_file: str = Field("database/tables.ods", description="Path to the tables file")
    small_template: str = Field("templates/contractor-daily-labor-template.ods", description="Small template (up to 7 rows)")
    medium_template: str = Field("templates/medium_template.ots", description="Medium template (8-20 rows)")
    large_template: str = Field("templates/large_template.ots", description="Large template (21+ rows)")
    empty_day_template: str = Field("templates/empty-day.ots", description="Template for days with no labor")
    contractor_report_template: str = Field("templates/contractor_report_template.ots", description="Template for contractor period reports")
    row_thresholds: TemplateThresholdsConfig = Field(default_factory=TemplateThresholdsConfig)


class DatabaseConfig(BaseModel):
    """Database configuration."""

    path: str = Field("database/labor_reports.db", description="Path to SQLite database file")


class HistoryConfig(BaseModel):
    """History file configuration."""

    file: str = Field("database/history.ods", description="Path to history file")


class TablesConfig(BaseModel):
    """Configuration for sheets in tables.ods."""

    contractor_sheet: str = Field("tblContractor", description="Sheet name for contractors")
    zones_sheet: str = Field("tblZones", description="Sheet name for work zones")


class SessionConfig(BaseModel):
    """Configuration for user session management."""

    timeout_minutes: int = Field(30, description="Session idle timeout in minutes", ge=1)
    cleanup_interval_seconds: int = Field(60, description="Interval between cleanup cycles", ge=5)


class LifecycleConfig(BaseModel):
    """Report lifecycle configuration. (NEW v2.0)

    Controls automatic transitions of report status over time:
    - ``auto_lock_hours``: Automatically lock finalized reports after N hours.
    - ``max_versions``: Maximum version snapshots kept per report.
    - ``auto_finalize_hour`` / ``auto_finalize_minute``: Time to auto-finalize
      all draft reports for today (e.g. 17:00 = 5 PM deadline).
      Set auto_finalize_hour < 0 to disable auto-finalization.
    """

    auto_lock_hours: int = Field(24, description="Auto-lock reports after N hours (0=disabled)", ge=0)
    max_versions: int = Field(50, description="Maximum version snapshots per report", ge=1)
    auto_finalize_hour: int = Field(17, description="Hour to auto-finalize today's drafts (24h, -1=disabled)", ge=-1, le=23)
    auto_finalize_minute: int = Field(0, description="Minute for auto-finalization", ge=0, le=59)


class ValidationConfig(BaseModel):
    """Validation rules configuration. (NEW v2.0 â€” mventor-ticket-013)"""

    max_workers_per_contractor: int = Field(100, description="Warn if exceeded", ge=1)
    max_contractors_per_report: int = Field(50, description="Warn if exceeded", ge=1)
    min_workers_per_contractor: int = Field(1, description="Warn if below", ge=0)
    warn_duplicate_contractors: bool = Field(True, description="Warn if same contractor added twice")
    warn_empty_details: bool = Field(True, description="Warn if no details provided")
    project_start_date: str = Field("2020-01-01", description="Earliest valid report date (YYYY-MM-DD)")
    max_future_days: int = Field(7, description="Warn if date is more than this many days in the future", ge=0)


class StatisticsConfig(BaseModel):
    """Statistics configuration. (NEW v2.0)"""

    cache_ttl_minutes: int = Field(60, description="How long to cache stats", ge=1)
    top_n_contractors: int = Field(10, description="Number of top contractors to track", ge=1)
    top_n_zones: int = Field(10, description="Number of top zones to track", ge=1)


class ReportingWindowConfig(BaseModel):
    """Reporting time window configuration. (NEW)

    Defines the daily window during which users can submit/edit reports.
    Times are in the configured timezone.
    """

    start_hour: int = Field(9, description="Window start hour (24h)", ge=0, le=23)
    start_minute: int = Field(0, description="Window start minute", ge=0, le=59)
    end_hour: int = Field(14, description="Window end hour (24h)", ge=0, le=23)
    end_minute: int = Field(0, description="Window end minute", ge=0, le=59)


class DashboardConfig(BaseModel):
    """Dashboard configuration. (NEW v2.0)"""

    show_time_remaining: bool = Field(True, description="Show countdown to deadline")
    deadline_hour: int = Field(14, description="Submission deadline hour (24h)", ge=0, le=23)
    deadline_minute: int = Field(0, description="Submission deadline minute", ge=0, le=59)


class ProjectConfig(BaseModel):
    """Project/site configuration for PDF filename generation."""

    name: str = Field("site", description="Project name used in PDF filenames (DD-MM-YYYY_{name}_labor_report.pdf)")


class SuggestionsConfig(BaseModel):
    """Smart suggestion configuration."""

    max_suggestions: int = Field(10, description="Max suggestions to return", ge=1)
    recent_days: int = Field(30, description="Lookback window for recent contractors in days", ge=1)
    frequent_limit: int = Field(5, description="Number of globally frequent contractors to include", ge=0)


class TimezoneConfig(BaseModel):
    """Timezone and holiday enforcement configuration. (NEW)

    Uses IANA timezone names (e.g., 'Asia/Riyadh', 'America/New_York', 'Asia/Tokyo').

    ``holidays_and_friday`` controls whether the bot enforces holidays and
    Fridays as non-working days:
      - ``"enabled"`` (default): holidays/Friday auto-create empty PDFs;
        bot skips reminders and blocks editing on those days.
      - ``"disabled"``: holidays/Friday are treated as normal working days;
        bot operates normally.
    """

    name: str = Field("Asia/Riyadh", description="IANA timezone name for scheduling")
    holidays_and_friday: str = Field(
        "enabled",
        description="'enabled' to enforce holidays/Friday, 'disabled' to treat as working days",
    )


class NotificationConfig(BaseModel):
    """Notification schedule configuration. (NEW)

    Configures automated notifications sent to users at specific times.
    All times are in the configured timezone.
    """

    enabled: bool = Field(True, description="Master switch for notifications")
    morning_reminder_hour: int = Field(9, description="Hour for morning reminder (24h)", ge=0, le=23)
    morning_reminder_minute: int = Field(0, description="Minute for morning reminder", ge=0, le=59)
    late_morning_reminder_hour: int = Field(11, description="Hour for late morning reminder (24h)", ge=0, le=23)
    late_morning_reminder_minute: int = Field(0, description="Minute for late morning reminder", ge=0, le=59)
    afternoon_reminder_hour: int = Field(14, description="Hour for afternoon reminder (24h)", ge=0, le=23)
    afternoon_reminder_minute: int = Field(0, description="Minute for afternoon reminder", ge=0, le=59)
    auto_no_report_hour: int = Field(17, description="Hour to auto-create no_report (24h)", ge=0, le=23)
    auto_no_report_minute: int = Field(0, description="Minute for auto no_report generation", ge=0, le=59)
    empty_template_file: str = Field("templates/empty-day.ots", description="Template for empty/no_report days")
    check_interval_seconds: int = Field(30, description="How often to check time (seconds)", ge=10, le=300)
    send_to_admin_only: bool = Field(False, description="If true, only sends notifications to admin users")


class AppConfig(BaseModel):
    """Root configuration model for Labor-Report.

    All settings are loaded from config.yaml.
    """

    template: TemplateConfig = Field(default_factory=TemplateConfig)
    date: dict = Field(default_factory=lambda: {"cell": "B7", "day_cell": "B5"})
    table: TableConfig = Field(default_factory=TableConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tables: TablesConfig = Field(default_factory=TablesConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)        # NEW v2.0
    validation: ValidationConfig = Field(default_factory=ValidationConfig)      # NEW v2.0
    statistics: StatisticsConfig = Field(default_factory=StatisticsConfig)      # NEW v2.0
    reporting_window: ReportingWindowConfig = Field(default_factory=ReportingWindowConfig)  # NEW
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)        # NEW v2.0
    suggestions: SuggestionsConfig = Field(default_factory=SuggestionsConfig)  # NEW v2.0 â€” mventor-ticket-009
    timezone: TimezoneConfig = Field(default_factory=TimezoneConfig)          # NEW
    notification: NotificationConfig = Field(default_factory=NotificationConfig)  # NEW
    auth: dict = Field(default_factory=dict)  # NEW v3.0 â€” loaded from config.yaml
    project: ProjectConfig = Field(default_factory=ProjectConfig)  # NEW

    @property
    def super_admin_chat_id(self) -> str:
        """Get the super admin Telegram chat ID from config."""
        value = self.auth.get("superadmin")
        if value is None:
            logger.warning("No superadmin configured in auth.superadmin")
            return ""
        return str(value)

    @property
    def admin_chat_ids(self) -> list[str]:
        """Get additional admin chat IDs from config."""
        raw = self.auth.get("admins", [])
        if isinstance(raw, list):
            return [str(cid).strip() for cid in raw if str(cid).strip()]
        return []

    @property
    def template_path(self) -> Path:
        """Get the resolved template file path (legacy fallback)."""
        return Path(self.template.file).resolve()

    def get_template_for_row_count(self, row_count: int) -> Path:
        """Get the appropriate template path based on number of data rows.

        Args:
            row_count: Number of contractor rows in the report.

        Returns:
            Path to the appropriate template file.
            When row_count is 0, returns the empty_day_template.
        """
        if row_count == 0:
            return self.empty_day_template_path
        thresholds = self.template.row_thresholds
        if row_count > thresholds.large:
            return Path(self.template.large_template).resolve()
        elif row_count > thresholds.medium:
            return Path(self.template.medium_template).resolve()
        return Path(self.template.small_template).resolve()

    @property
    def small_template_path(self) -> Path:
        """Get the resolved small template path."""
        return Path(self.template.small_template).resolve()

    @property
    def medium_template_path(self) -> Path:
        """Get the resolved medium template path."""
        return Path(self.template.medium_template).resolve()

    @property
    def large_template_path(self) -> Path:
        """Get the resolved large template path."""
        return Path(self.template.large_template).resolve()

    @property
    def empty_day_template_path(self) -> Path:
        """Get the resolved empty day template path."""
        return Path(self.template.empty_day_template).resolve()

    @property
    def contractor_report_template_path(self) -> Path:
        """Get the resolved contractor report template path."""
        return Path(self.template.contractor_report_template).resolve()

    @property
    def tables_file_path(self) -> Path:
        """Get the resolved tables file path."""
        return Path(self.template.tables_file).resolve()

    @property
    def date_cell(self) -> str:
        """Get the date cell reference."""
        return self.date.get("cell", "B4")

    @property
    def day_cell(self) -> str:
        """Get the day cell reference."""
        return self.date.get("day_cell", "D4")

    @property
    def pdf_folder_path(self) -> Path:
        """Get the resolved PDF output folder path."""
        return Path(self.output.pdf_folder).resolve()

    @property
    def docs_folder_path(self) -> Path:
        """Get the resolved generated-documents folder path."""
        return Path(self.output.docs_folder).resolve()

    @property
    def preview_folder_path(self) -> Path:
        """Get the resolved preview PDF output folder path."""
        return Path(self.output.preview_folder).resolve()

    @property
    def pdf_database_folder_path(self) -> Path:
        """Get the resolved PDF database folder path."""
        return Path(self.output.pdf_database).resolve()

    @property
    def reporting_window_start(self) -> time:
        """Get the reporting window start time."""
        return time(self.reporting_window.start_hour, self.reporting_window.start_minute)

    @property
    def reporting_window_end(self) -> time:
        """Get the reporting window end time."""
        return time(self.reporting_window.end_hour, self.reporting_window.end_minute)

    @property
    def database_path(self) -> Path:
        """Get the resolved database file path."""
        return Path(self.database.path).resolve()

    @property
    def history_path(self) -> Path:
        """Get the resolved history file path."""
        return Path(self.history.file).resolve()
