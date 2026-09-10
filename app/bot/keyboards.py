from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _get_role_level(role: str) -> int:
    """Get numeric role level for permission comparison.

    Args:
        role: User role string.

    Returns:
        Numeric level (higher = more privileges).
    """
    levels = {
        "superadmin": 100,
        "project_manager": 100,
        "hr": 100,
        "executive_engineer": 80,
        "admin": 80,
        "normal_user": 40,
        "viewer": 20,
        "pending": 0,
        "rejected": 0,
    }
    return levels.get(role, 0)


def hr_menu_keyboard(role: str = "pending") -> InlineKeyboardMarkup:
    """HR menu: request buttons for everyone, queue for PM/HR/superadmin."""
    level = _get_role_level(role)
    keyboard = [
        [
            InlineKeyboardButton("Request Advance", callback_data="hr_new_advance"),
            InlineKeyboardButton("Request Transport", callback_data="hr_new_transport"),
        ],
        [InlineKeyboardButton("My Requests", callback_data="hr_my")],
    ]
    if level >= 100 and role in ("superadmin", "project_manager", "hr"):
        keyboard.append([InlineKeyboardButton("Approval Queue", callback_data="hr_pending")])
    return InlineKeyboardMarkup(keyboard)


def hr_month_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Next 6 deduction months as buttons (advance approval)."""
    from datetime import date

    today = date.today()
    buttons = []
    row = []
    year, month = today.year, today.month
    for _ in range(6):
        key = f"{year:04d}-{month:02d}"
        row.append(InlineKeyboardButton(key, callback_data=f"hr_month:{key}:{request_id}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
        month += 1
        if month > 12:
            month, year = 1, year + 1
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def main_menu_keyboard(report_status: str, role: str = "pending", has_reports: bool = False, is_business_hours: bool = True) -> InlineKeyboardMarkup:
    """Build main menu keyboard based on user role, report status, and business hours.

    Args:
        report_status: Current report status string.
        role: User's role (superadmin, admin, normal_user, viewer).
        has_reports: Whether any reports exist in the database.
        is_business_hours: Whether current time is within business hours (8 AM - 5 PM).

    Returns:
        InlineKeyboardMarkup with role-appropriate buttons.
    """
    role_level = _get_role_level(role)
    buttons = []
    can_create = role_level >= 40 and is_business_hours  # normal_user+ AND business hours
    can_view = role_level >= 20    # viewer+
    is_admin = role_level >= 80    # admin+

    # Report status-specific buttons
    if can_create:
        if report_status in ("not_created", "no_report"):
            buttons.extend([
                InlineKeyboardButton("Create Report", callback_data="create_report"),
                InlineKeyboardButton("Copy Yesterday", callback_data="copy_yesterday"),
            ])
        elif report_status == "draft":
            buttons.extend([
                InlineKeyboardButton("Open Draft", callback_data="open_draft"),
                InlineKeyboardButton("Preview PDF", callback_data="preview_pdf"),
            ])
            if can_create:
                buttons.append(InlineKeyboardButton("Finalize", callback_data="finalize"))
            if is_admin:
                buttons.append(InlineKeyboardButton("Revert Full Report", callback_data="revert_report"))
            elif can_create:
                buttons.append(InlineKeyboardButton("Revert Last", callback_data="revert_last"))
        elif report_status == "final":
            buttons.extend([
                InlineKeyboardButton("View Report", callback_data="view_report"),
                InlineKeyboardButton("Download PDF", callback_data="download_pdf"),
            ])
            if is_admin:
                buttons.append(InlineKeyboardButton("Lock", callback_data="lock"))
        elif report_status == "locked":
            buttons.append(InlineKeyboardButton("View Report", callback_data="view_report"))
            if is_admin:
                buttons.append(InlineKeyboardButton("Unlock", callback_data="unlock"))
    elif can_view:
        # Viewer can only view
        if report_status in ("draft", "final", "locked"):
            buttons.append(InlineKeyboardButton("View Report", callback_data="view_report"))
            if report_status != "draft":
                buttons.append(InlineKeyboardButton("Download PDF", callback_data="download_pdf"))

    # Common buttons for all authorized users
    if can_view or has_reports:
        buttons.append(InlineKeyboardButton("Search Reports", callback_data="search"))

    if can_view:
        # Contractor reports for any viewer+
        buttons.append(InlineKeyboardButton("Contractor Reports", callback_data="contractor_reports"))

    # Help always available
    buttons.append(InlineKeyboardButton("Help", callback_data="help"))

    # Admin panel for admin+
    if is_admin:
        buttons.append(InlineKeyboardButton("Admin Panel", callback_data="admin_panel"))

    # Build keyboard in rows of 2
    keyboard = []
    row = []
    for btn in buttons:
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Add Main Menu button to all keyboards
    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])
    
    return InlineKeyboardMarkup(keyboard)


def report_actions_keyboard(report_status: str, role: str = "pending") -> InlineKeyboardMarkup:
    """Build report action keyboard based on user role.

    Args:
        report_status: Current report status ('draft', 'final', 'locked').
        role: User's role.

    Returns:
        InlineKeyboardMarkup with action buttons.
    """
    role_level = _get_role_level(role)
    can_create = role_level >= 40
    is_admin = role_level >= 80
    keyboard = []

    if report_status == "draft":
        if can_create:
            keyboard.append([InlineKeyboardButton("Add Contractor", callback_data="add_contractor")])
            keyboard.append([InlineKeyboardButton("Preview PDF", callback_data="preview_pdf")])
            keyboard.append([InlineKeyboardButton("Finalize", callback_data="finalize")])
            if is_admin:
                keyboard.append([InlineKeyboardButton("Revert Full Report", callback_data="revert_report")])
            else:
                keyboard.append([InlineKeyboardButton("Revert Last", callback_data="revert_last")])
    elif report_status == "final":
        keyboard.append([InlineKeyboardButton("Download PDF", callback_data="download_pdf")])
        if is_admin:
            keyboard.append([InlineKeyboardButton("Lock", callback_data="lock")])
    elif report_status == "locked":
        keyboard.append([InlineKeyboardButton("View Report", callback_data="view_report")])
        if is_admin:
            keyboard.append([InlineKeyboardButton("Unlock", callback_data="unlock")])

    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])
    return InlineKeyboardMarkup(keyboard)


def contractor_selection_keyboard(
    contractors: list[tuple[str, str]],
    page: int = 0,
    total_pages: int = 1,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    """Build a paginated contractor selection keyboard."""
    keyboard = []

    start = page * page_size
    end = start + page_size
    page_contractors = contractors[start:end]

    for name, code in page_contractors:
        keyboard.append(
            [InlineKeyboardButton(name, callback_data=f"select_contractor:{code}")]
        )

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("Previous", callback_data=f"contractor_page:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next", callback_data=f"contractor_page:{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_report")])
    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])

    return InlineKeyboardMarkup(keyboard)


def zone_selection_keyboard(zones: list[str]) -> InlineKeyboardMarkup:
    """Build a keyboard to select a work zone or skip."""
    keyboard = []
    for zone in zones:
        keyboard.append(
            [InlineKeyboardButton(zone, callback_data=f"select_zone:{zone}")]
        )
    keyboard.append([InlineKeyboardButton("Skip", callback_data="select_zone:__skip__")])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_report")])
    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])
    return InlineKeyboardMarkup(keyboard)


def admin_keyboard(role: str = "pending") -> InlineKeyboardMarkup:
    """Build admin panel keyboard based on role."""
    role_level = _get_role_level(role)
    is_admin = role_level >= 80
    keyboard = []

    keyboard.append([InlineKeyboardButton("View Events", callback_data="admin_events")])
    keyboard.append([InlineKeyboardButton("Health Check", callback_data="admin_health")])
    keyboard.append([InlineKeyboardButton("Auto-Lock Reports", callback_data="admin_autolock")])

    # User management only for admin+
    if is_admin:
        keyboard.append([InlineKeyboardButton("User Management", callback_data="admin_users")])

    # Role management GUI (for admin+)
    if is_admin:
        keyboard.append([InlineKeyboardButton("Role Manager GUI", callback_data="admin_role_gui")])

    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])
    return InlineKeyboardMarkup(keyboard)


def confirmation_keyboard(action: str) -> InlineKeyboardMarkup:
    """Build a confirm/cancel keyboard with Main Menu navigation."""
    keyboard = [
        [
            InlineKeyboardButton("Confirm", callback_data=f"confirm:{action}"),
            InlineKeyboardButton("Cancel", callback_data=f"cancel:{action}"),
        ],
        [InlineKeyboardButton("Main Menu", callback_data="dashboard")],
    ]
    return InlineKeyboardMarkup(keyboard)


def search_results_keyboard(
    results: list[dict],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Build paginated search results keyboard."""
    keyboard = []

    for result in results:
        date = result["date"]
        count = result.get("contractor_count", "?")
        status = result.get("status")
        text = f"{date} - {count} contractors"
        if status:
            text = f"{text} ({status})"
        keyboard.append(
            [InlineKeyboardButton(text, callback_data=f"view_report:{date}")]
        )

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("Previous", callback_data=f"search_page:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next", callback_data=f"search_page:{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("New Search", callback_data="new_search")])
    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])

    return InlineKeyboardMarkup(keyboard)


def contractor_reports_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for contractor report period selection."""
    keyboard = [
        [InlineKeyboardButton("This Week", callback_data="report_period:this_week")],
        [InlineKeyboardButton("Last Week", callback_data="report_period:last_week")],
        [InlineKeyboardButton("This Month", callback_data="report_period:this_month")],
        [InlineKeyboardButton("Last Month", callback_data="report_period:last_month")],
        [InlineKeyboardButton("Main Menu", callback_data="dashboard")],
    ]
    return InlineKeyboardMarkup(keyboard)


def contractor_selection_keyboard_for_report(contractors: list[str], max_per_row: int = 2) -> InlineKeyboardMarkup:
    """Build keyboard for contractor selection in contractor reports.

    Uses index-based callback_data (``rc:N``) to stay well within
    Telegram's 64-byte callback_data limit.

    Args:
        contractors: List of contractor names.
        max_per_row: Maximum contractors per row.

    Returns:
        InlineKeyboardMarkup with contractor selection buttons.
    """
    keyboard = []
    row = []
    for idx, contractor in enumerate(contractors):
        row.append(InlineKeyboardButton(contractor, callback_data=f"rc:{idx}"))
        if len(row) == max_per_row:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Add navigation buttons
    keyboard.append([InlineKeyboardButton("Back to Periods", callback_data="contractor_reports")])
    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="dashboard")])

    return InlineKeyboardMarkup(keyboard)


# role_manager_keyboard and role_change_keyboard removed in mventor-ticket-031
# (dead code â€” no handlers were registered for their callback patterns)
