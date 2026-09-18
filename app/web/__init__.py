"""Atlas HQ Web App factory (ticket-037).

Same domain services as the Telegram bot (same constructors as main.py),
exposed through an authorized Flask layer. No business logic here: routes
resolve identity -> memberships -> site -> capability -> object, then call
domain services and render the result.
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, g, request

from app.config.loader import ConfigLoader
from app.database.manager import DatabaseManager
from app.repositories.attendance_day_repository import AttendanceDayRepository
from app.repositories.attendance_repository import AttendanceRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.discipline_repository import DisciplineRepository
from app.repositories.event_log_repository import EventLogRepository
from app.repositories.hr_repository import HRRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.money_repository import MoneyRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.payroll_repository import PayrollRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.user_repository import UserRepository
from app.services.attendance_day_service import AttendanceDayService
from app.services.attendance_service import AttendanceService
from app.services.audit_service import AuditService
from app.services.authorization_service import AuthorizationService
from app.services.case_service import CaseService
from app.services.discipline_service import DisciplineService
from app.services.event_log_service import EventLogService
from app.services.hr_service import HRService
from app.services.notification_outbox import NotificationOutbox
from app.services.payroll_service import PayrollService
from app.services.report_workflow_service import ReportWorkflowService
from app.services.working_calendar import WorkingCalendar
from app.web import auth as webauth
from app.web import authz
from app.web import i18n
from app.web.db import ensure_web_tables


def build_services(config, db_manager):
    """Mirror main.py construction so web enforces identical rules."""
    user_repo = UserRepository(db_manager)
    membership_repo = MembershipRepository(db_manager)
    auth_svc = AuthorizationService(
        user_repo=user_repo,
        super_admin_chat_id=config.super_admin_chat_id,
        admin_chat_ids=config.admin_chat_ids,
        membership_repo=membership_repo,
    )
    hr_repo = HRRepository(db_manager)
    money_repo = MoneyRepository(db_manager)
    day_repo = AttendanceDayRepository(db_manager)
    att_repo = AttendanceRepository(db_manager)
    case_repo = CaseRepository(db_manager)
    discipline_repo = DisciplineRepository(db_manager)
    payroll_repo = PayrollRepository(db_manager)
    return {
        "config": config,
        "db": db_manager,
        "user_repo": user_repo,
        "membership_repo": membership_repo,
        "auth": auth_svc,
        "hr": HRService(hr_repo, money_repo),
        "money_repo": money_repo,
        "cases": CaseService(case_repo),
        "case_repo": case_repo,
        "discipline": DisciplineService(discipline_repo),
        "discipline_repo": discipline_repo,
        "payroll": PayrollService(payroll_repo, user_repo,
                                  membership_repo=membership_repo,
                                  hr_repo=hr_repo, money_repo=money_repo,
                                  day_repo=day_repo),
        "payroll_repo": payroll_repo,
        "events": EventLogService(db_manager),
        "audit": AuditService(db_manager),
        "outbox": NotificationOutbox(
            NotificationRepository(db_manager),
            max_attempts=config.notification.notify_max_attempts,
            backoff_min=tuple(config.notification.notify_retry_backoff_min)),
        "attendance": AttendanceService(att_repo),
        "attendance_days": AttendanceDayService(
            day_repo, att_repo,
            calendar_for=lambda site: WorkingCalendar(config, site)),
        "reports": ReportRepository(db_manager, EventLogRepository(db_manager)),
        "workflow": ReportWorkflowService(
            ReportRepository(db_manager, EventLogRepository(db_manager)),
            EventLogService(db_manager), config),
    }


def create_app(config_path: str | Path | None = None) -> Flask:
    config = ConfigLoader.load(config_path)
    db_manager = DatabaseManager(str(config.database_path))
    try:
        db_manager.run_migration()
    except Exception:
        pass
    ensure_web_tables(db_manager)
    services = build_services(config, db_manager)

    root = Path(__file__).parent
    app = Flask(__name__, template_folder=str(root / "templates"),
                static_folder=str(root / "static"))
    # Flask signs its (tiny) session cookie with this. Derive from the host
    # BOT_TOKEN: already a host secret, never committed, never rendered.
    try:
        app.secret_key = "atlas-hq:" + ConfigLoader.get_bot_token()
    except Exception:
        app.secret_key = "atlas-hq-dev-only-change-me"
    app.extensions["atlas_db"] = db_manager
    app.extensions["atlas_auth"] = services["auth"]
    app.extensions["atlas_services"] = services

    from app.web.views_auth import bp as auth_bp
    from app.web.views_dev import bp as dev_bp
    from app.web.views_hr import bp as hr_bp
    from app.web.views_setup import bp as setup_bp
    from app.web.views_shell import bp as shell_bp
    from app.web.views_shell import module_url

    app.register_blueprint(auth_bp)
    app.register_blueprint(hr_bp)
    app.register_blueprint(dev_bp)
    app.register_blueprint(setup_bp)
    app.register_blueprint(shell_bp)
    app.template_global("module_url")(module_url)

    @app.before_request
    def _load_identity():
        authz.load_identity()

    @app.context_processor
    def _inject_ui():
        lang = i18n.get_lang()
        ctx = {
            "lang": lang,
            "dir": "rtl" if lang == "ar" else "ltr",
            "t": i18n.t,
            "nav": i18n.nav_items(lang),
            "sections": i18n.section_items(lang),
            "slug_label": (lambda s: i18n.slug_label(s, lang)),
            "theme": request.cookies.get("atlas_theme", "system"),
            "setup_banner": False,
            "setup_remaining": 0,
        }
        try:
            chat_id = authz.current_chat_id()
            if chat_id:
                services = app.extensions["atlas_services"]
                from app.web.setup import get_manual_ack, setup_status
                st = setup_status(services, chat_id,
                                  get_manual_ack(services["db"], chat_id))
                ctx["setup_banner"] = not st["setup_complete"]
                ctx["setup_remaining"] = (st["core_total"] - st["core_done"])
        except Exception:
            pass
        return ctx

    @app.errorhandler(403)
    def _forbidden(_e):
        from flask import render_template
        return render_template("error.html", code=403,
                               message=i18n.t("forbidden")), 403

    @app.errorhandler(404)
    def _missing(_e):
        from flask import render_template
        return render_template("error.html", code=404,
                               message=i18n.t("not_found")), 404

    return app
