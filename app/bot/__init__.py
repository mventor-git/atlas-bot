"""Telegram bot package for Labor-Report.

Handles all Telegram user interactions and conversations.
"""

import os
from pathlib import Path

from telegram.ext import Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler

from app.utils.logger import get_logger
from app.utils.exceptions import ConfigurationError

logger = get_logger(__name__)


def _load_token() -> str:
    """Load the bot token from environment."""
    token = os.environ.get("BOT_TOKEN")
    if not token:
        # Try reading from .env file
        env_path = Path(".env")
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        break

    if not token:
        raise ConfigurationError(
            "BOT_TOKEN is not set. Set it in .env or as an environment variable."
        )
    return token


def create_bot_app(
    report_repository,
    event_log_service,
    workflow_service,
    auto_save_service,
    suggestion_service,
    contractor_search,
    one_click_yesterday_service,
    daily_dashboard_service,
    universal_search_service,
    arabic_date_service,
    validation_service,
    pdf_preview_service,
    app_config,
    authorization_service,
    audit_service=None,
    add_contractor_service=None,
    notification_manager=None,
    daily_comparison_service=None,
    hr_service=None,
) -> Application:
    """Create and configure the Telegram bot application.

    Args:
        Various service instances to inject into bot_data.
        authorization_service: AuthorizationService for role management.
        audit_service: Optional AuditService for user activity tracking.
        notification_manager: Optional NotificationManager for broadcasts.

    Returns:
        Configured Application (not running).
    """
    token = _load_token()
    builder = ApplicationBuilder().token(token)

    app = builder.build()

    # Store services in bot_data for handlers to access
    app.bot_data["report_repository"] = report_repository
    app.bot_data["event_log_service"] = event_log_service
    app.bot_data["workflow_service"] = workflow_service
    app.bot_data["auto_save_service"] = auto_save_service
    app.bot_data["suggestion_service"] = suggestion_service
    app.bot_data["contractor_search"] = contractor_search
    app.bot_data["one_click_yesterday_service"] = one_click_yesterday_service
    app.bot_data["daily_dashboard_service"] = daily_dashboard_service
    app.bot_data["universal_search_service"] = universal_search_service
    app.bot_data["arabic_date_service"] = arabic_date_service
    app.bot_data["validation_service"] = validation_service
    app.bot_data["pdf_preview_service"] = pdf_preview_service
    app.bot_data["authorization_service"] = authorization_service
    app.bot_data["audit_service"] = audit_service
    app.bot_data["add_contractor_service"] = add_contractor_service
    app.bot_data["notification_manager"] = notification_manager
    app.bot_data["daily_comparison_service"] = daily_comparison_service
    app.bot_data["hr_service"] = hr_service
    app.bot_data["app_config"] = app_config

    logger.info(
        "Bot application created with authorization service (superadmin=%s)",
        authorization_service._super_admin_chat_id,  # pylint: disable=protected-access
    )

    _register_handlers(app)
    return app


def _register_handlers(app: Application) -> None:
    """Register all command and callback handlers from handler modules."""
    # Import handler registration functions
    from app.bot.handlers.start import get_registration_handlers as get_start_handlers
    from app.bot.handlers.report_create import get_registration_handlers as get_report_handlers
    from app.bot.handlers.search import get_registration_handlers as get_search_handlers
    from app.bot.handlers.admin import get_registration_handlers as get_admin_handlers
    from app.bot.handlers.admin_users import get_registration_handlers as get_admin_users_handlers
    from app.bot.handlers.report_retrieval import get_registration_handlers as get_retrieval_handlers
    from app.bot.handlers.comparison import get_registration_handlers as get_comparison_handlers
    from app.bot.handlers.hr import get_registration_handlers as get_hr_handlers

    handlers = []
    handlers.extend(get_start_handlers())
    handlers.extend(get_report_handlers())
    handlers.extend(get_search_handlers())
    handlers.extend(get_comparison_handlers())
    handlers.extend(get_hr_handlers())
    handlers.extend(get_admin_handlers())
    handlers.extend(get_admin_users_handlers())
    handlers.extend(get_retrieval_handlers())

    for handler in handlers:
        app.add_handler(handler)

    logger.info("Registered %d handlers", len(handlers))
