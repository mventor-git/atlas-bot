"""
Contractor-Bot Application Entry Point.

A Telegram-based construction daily labor tracker with period
contractor reports. Templates are LibreOffice-native (.ots).

Usage:
    python main.py              # Start the bot
    python main.py --health     # Run health check only
    python main.py --help       # Show help
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Fix for Python 3.14 asyncio event loop compatibility
# python-telegram-bot requires a running event loop
if sys.version_info >= (3, 14):
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass

# Ensure the project root is in sys.path
_project_root = Path(__file__).parent.resolve()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.config.loader import ConfigLoader, ConfigurationError
from app.database.manager import DatabaseManager
from app.repositories.report_repository import ReportRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.money_repository import MoneyRepository
from app.repositories.hr_repository import HRRepository
from app.repositories.recent_contractor_repository import RecentContractorRepository
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.search_repository import SearchRepository
from app.repositories.historical_search_repository import HistoricalSearchRepository
from app.repositories.favorites_repository import FavoritesRepository
from app.repositories.contractor_repository import ContractorRepository
from app.services.event_log_service import EventLogService
from app.services.report_workflow_service import ReportWorkflowService
from app.services.auto_save_service import AutoSaveService
from app.services.smart_suggestion_service import SmartSuggestionService
from app.services.contractor_search import ContractorSearchService
from app.services.one_click_yesterday_service import OneClickYesterdayService
from app.services.daily_dashboard_service import DailyDashboardService
from app.services.universal_search_service import UniversalSearchService
from app.services.arabic_date_service import ArabicDateService
from app.services.validation_service import ValidationService
from app.services.add_contractor_service import AddContractorService
from app.services.hr_service import HRService
from app.services.case_service import CaseService
from app.repositories.case_repository import CaseRepository
from app.services.discipline_service import DisciplineService
from app.repositories.discipline_repository import DisciplineRepository
from app.services.payroll_service import PayrollService
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.notification_outbox import NotificationOutbox
from app.services.attendance_service import AttendanceService
from app.repositories.attendance_repository import AttendanceRepository
from app.services.attendance_day_service import AttendanceDayService
from app.repositories.attendance_day_repository import AttendanceDayRepository
from app.services.working_calendar import WorkingCalendar
from app.services.audit_service import AuditService
from app.libre.filler import TemplateFiller
from app.libre.pdf import PDFGenerator
from app.services.pdf_preview_service import PDFPreviewService
from app.bot import create_bot_app
from app.repositories.user_repository import UserRepository
from app.services.authorization_service import AuthorizationService
from app.services.notification_manager import NotificationManager
from app.services.watchdog_service import WatchdogService
from app.services.daily_comparison_service import DailyComparisonService
from app.utils.business_hours import set_timezone as set_business_hours_tz
from app.utils.logger import setup_logger, get_logger


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed command line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Atlas-Bot - construction daily labor tracker with period contractor reports",
        epilog="For more information, see the documentation in docs/",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run a health check and exit",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to configuration file (default: config/config.yaml)",
    )
    return parser.parse_args()


def run_health_check(config) -> bool:
    """Run a comprehensive health check.

    Verifies:
    - Configuration loads correctly
    - Template file exists
    - Tables file exists
    - Output directories exist (or can be created)
    - Database directory exists (or can be created)
    - Log directory is writable

    Args:
        config: The application configuration.

    Returns:
        True if all checks pass, False otherwise.
    """
    logger = get_logger(__name__)
    logger.info("Running health check...")
    all_ok = True

    checks = [
        ("Configuration", True, "Configuration loaded successfully"),
        ("Template file", config.template_path.exists(), f"Template file: {config.template_path}"),
        ("Tables file", config.tables_file_path.exists(), f"Tables file: {config.tables_file_path}"),
    ]

    # Check directories (create if needed)
    dirs = [
        ("PDF output", config.pdf_folder_path),
        ("Documents output", config.docs_folder_path),
        ("Preview output", config.preview_folder_path),
        ("Database", config.database_path.parent),
        ("Logs", Path(config.logging.file).parent),
    ]

    for name, dir_path in dirs:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            checks.append((name, True, f"Directory ready: {dir_path}"))
        except OSError as e:
            checks.append((name, False, f"Cannot create directory: {dir_path} - {e}"))

    # Production template contract (030): fail fast with actionable lines.
    try:
        from app.libre.validate import TemplateValidator

        templates = (
            ("small", config.template.small_template),
            ("medium", config.template.medium_template),
            ("large", config.template.large_template),
        )
        validator = TemplateValidator(config)
        for tname, tpath in templates:
            report = validator.check_template(tpath)
            if report.passed:
                checks.append((f"Template {tname}", True, f"valid: {tpath}"))
            else:
                checks.append((f"Template {tname}", False,
                               "; ".join(str(f) for f in report.failures)))
    except Exception as e:
        checks.append(("Templates", False, f"validator error: {e}"))

    # Run checks
    for name, ok, detail in checks:
        status = "[OK]" if ok else "[FAIL]"
        logger.info("%s %s: %s", status, name, detail)
        if not ok:
            all_ok = False

    # Environment check
    try:
        token = ConfigLoader.get_bot_token()
        token_preview = token[:8] + "..." if len(token) > 8 else "present"
        logger.info("[OK] BOT_TOKEN: %s", token_preview)
    except ConfigurationError as e:
        logger.warning("[WARN] BOT_TOKEN: %s", e)

    # Database check
    try:
        db_manager = DatabaseManager(str(config.database_path))
        db_manager.close_all()
        logger.info("[OK] Database: connection successful")
    except Exception as e:
        logger.warning("[WARN] Database: %s", e)

    return all_ok


def main() -> None:
    """Main entry point for Atlas-Bot.

    Initializes configuration, logging, database, services,
    and starts the Telegram bot.
    """
    args = parse_args()

    # Initialize logger early
    logger = setup_logger()

    try:
        logger.info("=" * 60)
        logger.info("Atlas-Bot v2.0.0a1 starting...")
        logger.info("=" * 60)

        # Load configuration
        config = ConfigLoader.load(args.config)
        logger.info("Configuration loaded from: %s", args.config or ConfigLoader.DEFAULT_CONFIG_PATH)

        # Set timezone for business hours and notifications
        timezone_name = config.timezone.name
        set_business_hours_tz(timezone_name)
        logger.info("Timezone set to: %s", timezone_name)

        # Reconfigure logger with actual config
        logger = setup_logger(config.logging)
        logger.info("Logger reconfigured with: level=%s, file=%s", config.logging.level, config.logging.file)

        # Health check mode
        if args.health:
            logger.info("Running in health check mode...")
            ok = run_health_check(config)
            sys.exit(0 if ok else 1)

        # Ensure output directories exist
        config.pdf_folder_path.mkdir(parents=True, exist_ok=True)
        config.docs_folder_path.mkdir(parents=True, exist_ok=True)
        config.preview_folder_path.mkdir(parents=True, exist_ok=True)
        config.database_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("All directories verified.")

        # Initialize database
        logger.info("Initializing database...")
        db_manager = DatabaseManager(str(config.database_path))
        db_manager.run_migration()
        logger.info("Database initialized and migrated.")

        # Initialize repositories
        report_repo = ReportRepository(db_manager)
        recent_contractor_repo = RecentContractorRepository(db_manager)
        event_log_repo = EventLogRepository(db_manager)
        search_repo = SearchRepository(db_manager)
        historical_search_repo = HistoricalSearchRepository(db_manager)
        favorites_repo = FavoritesRepository(db_manager)
        contractor_repo = ContractorRepository(db_manager)  # mventor-ticket-036

        # Initialize repositories that need event_log_repo
        report_repo_with_events = ReportRepository(db_manager, event_log_repo)

        # Initialize services
        contractor_search = ContractorSearchService(config, recent_contractor_repo, contractor_repo)
        event_log_service = EventLogService(db_manager)
        validation_service = ValidationService(config)
        template_filler = TemplateFiller(config)
        pdf_generator = PDFGenerator(config)
        pdf_preview_service = PDFPreviewService(template_filler, pdf_generator, config)
        arabic_date_service = ArabicDateService()

        workflow_service = ReportWorkflowService(
            report_repo_with_events, event_log_service, config,
        )

        auto_save_service = AutoSaveService(
            report_repo_with_events, event_log_service,
        )

        suggestion_service = SmartSuggestionService(
            search_service=contractor_search,
            recent_repo=recent_contractor_repo,
            favorites_repo=favorites_repo,
            report_repo=report_repo_with_events,
            config=config,
        )

        one_click_yesterday_service = OneClickYesterdayService(
            report_repo_with_events,
        )

        daily_dashboard_service = DailyDashboardService(
            report_repo_with_events, config,
        )

        universal_search_service = UniversalSearchService(search_repo)
        audit_service = AuditService(db_manager)
        add_contractor_service = AddContractorService(contractor_repo, audit_service)  # mventor-ticket-036
        daily_comparison_service = DailyComparisonService(report_repo_with_events)  # mventor-ticket-019
        hr_service = HRService(HRRepository(db_manager), MoneyRepository(db_manager))  # HR advances + transport
        attendance_service = AttendanceService(AttendanceRepository(db_manager))
        day_repo = AttendanceDayRepository(db_manager)
        attendance_day_service = AttendanceDayService(
            day_repo, AttendanceRepository(db_manager),
            calendar_for=lambda site: WorkingCalendar(config, site))
        case_service = CaseService(CaseRepository(db_manager))
        discipline_service = DisciplineService(DisciplineRepository(db_manager))

        logger.info("All services initialized.")

        # Create and run bot
        logger.info("Creating bot application...")

        # Initialize authorization service
        user_repo = UserRepository(db_manager)
        membership_repo = MembershipRepository(db_manager)
        auth_service = AuthorizationService(
            user_repo=user_repo,
            super_admin_chat_id=config.super_admin_chat_id,
            admin_chat_ids=config.admin_chat_ids,
            membership_repo=membership_repo,
        )
        logger.info("Authorization service initialized (superadmin=%s)", config.super_admin_chat_id)
        if config.admin_chat_ids:
            logger.info("Additional admins from config: %s", config.admin_chat_ids)
        migrated = auth_service.migrate_memberships()
        if migrated:
            logger.info("Migrated %d legacy site memberships.", migrated)
        payroll_service = PayrollService(PayrollRepository(db_manager),
                                         user_repo,
                                         membership_repo=membership_repo)
        notification_outbox = NotificationOutbox(
            NotificationRepository(db_manager),
            max_attempts=config.notification.notify_max_attempts,
            backoff_min=tuple(config.notification.notify_retry_backoff_min))

        app = create_bot_app(
            report_repository=report_repo_with_events,
            event_log_service=event_log_service,
            workflow_service=workflow_service,
            auto_save_service=auto_save_service,
            suggestion_service=suggestion_service,
            contractor_search=contractor_search,
            one_click_yesterday_service=one_click_yesterday_service,
            daily_dashboard_service=daily_dashboard_service,
            universal_search_service=universal_search_service,
            arabic_date_service=arabic_date_service,
            validation_service=validation_service,
            pdf_preview_service=pdf_preview_service,
            app_config=config,
            authorization_service=auth_service,
            audit_service=audit_service,
            add_contractor_service=add_contractor_service,
            daily_comparison_service=daily_comparison_service,
            hr_service=hr_service,
            user_repository=user_repo,
            attendance_service=attendance_service,
            attendance_day_service=attendance_day_service,
            case_service=case_service,
            discipline_service=discipline_service,
            payroll_service=payroll_service,
            notification_outbox=notification_outbox,
        )

        # Wire notification manager & watchdog via post_init / post_stop
        _nm = None  # closure reference
        _wd = None  # closure reference

        async def _on_start(app_instance):
            nonlocal _nm, _wd
            # Start notification manager (shares the durable outbox)
            app_instance.bot_data["notification_outbox"] = notification_outbox
            _nm = NotificationManager(app_instance, db_manager, config,
                                      outbox=notification_outbox)
            _nm.start()
            app_instance.bot_data["notification_manager"] = _nm
            logger.info("Notification manager stored in bot_data")

            # Start watchdog service
            _wd = WatchdogService(
                application=app_instance,
                super_admin_chat_id=config.super_admin_chat_id,
                check_interval=30,
            )
            _wd.start()
            app_instance.bot_data["watchdog_service"] = _wd
            logger.info("Watchdog service started")

        async def _on_stop(app_instance):
            nonlocal _nm, _wd
            if _nm is not None:
                await _nm.stop()
            if _wd is not None:
                await _wd.stop()

        app.post_init = _on_start
        app.post_stop = _on_stop

        logger.info("=" * 60)
        logger.info("Atlas-Bot is running! Press Ctrl+C to stop.")
        logger.info("=" * 60)

        # Start polling (blocks until interrupted)
        app.run_polling(allowed_updates=None)

    except ConfigurationError as e:
        logger.critical("Configuration error: %s", e)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        print("\nBot stopped.")
    except Exception as e:
        logger.critical("Unexpected error during startup: %s", e, exc_info=True)
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
