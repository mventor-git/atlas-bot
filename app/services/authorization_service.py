"""
Authorization service for Labor-Report.

Manages user roles and access control:
- superadmin: full control, can manage admins
- project_manager: like superadmin (full control, can promote/demote)
- executive_engineer: admin-level access (finalize, manage users), cannot promote/demote
- admin: can finalize reports, approve users, manage all reports
- normal_user: can create/edit drafts only
- viewer: can only view reports and search, cannot create/edit
- pending: awaiting admin approval
- rejected: denied access
"""

from datetime import datetime
from typing import Optional

from app.models.database import User
from app.repositories.user_repository import UserRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AuthorizationService:
    """Service for user authorization and role management."""

    # All valid roles
    ADMIN_ROLES = ("superadmin", "project_manager", "executive_engineer", "admin", "hr")
    APPROVED_ROLES = ("superadmin", "project_manager", "executive_engineer", "admin", "hr", "normal_user", "viewer")
    CAN_FINALIZE_ROLES = ("superadmin", "project_manager", "executive_engineer", "admin", "hr")
    CAN_MANAGE_USERS_ROLES = ("superadmin", "project_manager", "executive_engineer", "admin", "hr")
    CAN_PROMOTE_ROLES = ("superadmin", "project_manager")
    CAN_CONFIRM_PM_ROLES = ("superadmin", "project_manager")
    CAN_APPROVE_HR_ROLES = ("superadmin", "hr")
    CAN_CREATE_REPORTS_ROLES = ("superadmin", "project_manager", "executive_engineer", "admin", "hr", "normal_user")
    CAN_VIEW_ROLES = ("superadmin", "project_manager", "executive_engineer", "admin", "hr", "normal_user", "viewer")

    def __init__(self, user_repo: UserRepository,
                 super_admin_chat_id: str = "",
                 admin_chat_ids: Optional[list[str]] = None,
                 membership_repo=None) -> None:
        """Initialize the authorization service.

        Args:
            user_repo: The UserRepository instance.
            super_admin_chat_id: Telegram chat ID of the super admin.
            admin_chat_ids: Additional admin chat IDs from config.
        """
        self._repo = user_repo
        self._super_admin_chat_id = super_admin_chat_id
        self._extra_admin_ids = set(admin_chat_ids or [])
        self._memberships = membership_repo

    # --- Public API ---

    def get_role(self, chat_id: str) -> str:
        """Get the role of a user.

        Always ensures the super admin has the superadmin role.
        Additional admins from config are ensured admin role.
        Unknown users are treated as 'pending'.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            Role string: 'superadmin', 'project_manager', 'executive_engineer', 'admin', 'hr', 'normal_user', 'viewer', 'pending', or 'rejected'.
        """
        # Super admin is always superadmin
        if chat_id == self._super_admin_chat_id:
            return "superadmin"

        # Check if this is a configured admin
        if chat_id in self._extra_admin_ids:
            user = self._repo.get_by_chat_id(chat_id)
            if user is None:
                return "admin"
            return user.role

        user = self._repo.get_by_chat_id(chat_id)
        if user is None:
            return "pending"
        return user.role

    def is_admin(self, chat_id: str) -> bool:
        """Check if a user has admin-level access (superadmin, project_manager, executive_engineer, or admin).

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user is superadmin, project_manager, executive_engineer, or admin.
        """
        role = self.get_role(chat_id)
        return role in self.ADMIN_ROLES

    def is_super_admin(self, chat_id: str) -> bool:
        """Check if a user is the super admin.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user is the super admin.
        """
        return chat_id == self._super_admin_chat_id

    def is_authorized(self, chat_id: str) -> bool:
        """Check if a user is authorized to use the bot.

        Authorized means: superadmin, project_manager, executive_engineer, admin, normal_user, or viewer.
        Pending and rejected users are not authorized.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user can use the bot.
        """
        role = self.get_role(chat_id)
        return role in self.APPROVED_ROLES

    def can_view_reports(self, chat_id: str) -> bool:
        """Check if a user can view/search reports (viewer+).

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user has view access.
        """
        role = self.get_role(chat_id)
        return role in self.CAN_VIEW_ROLES

    def can_create_reports(self, chat_id: str) -> bool:
        """Check if a user can create/edit draft reports (normal_user+).

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user has create/edit access.
        """
        role = self.get_role(chat_id)
        return role in self.CAN_CREATE_REPORTS_ROLES

    def can_finalize(self, chat_id: str) -> bool:
        """Check if a user can finalize/lock reports.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user is admin or superadmin.
        """
        role = self.get_role(chat_id)
        return role in self.CAN_FINALIZE_ROLES

    def can_manage_users(self, chat_id: str) -> bool:
        """Check if a user can manage other users (approve/reject).

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the user is admin or superadmin.
        """
        role = self.get_role(chat_id)
        return role in self.CAN_MANAGE_USERS_ROLES

    def can_promote_demote(self, chat_id: str) -> bool:
        """Check if a user can promote/demote admins.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True for the superadmin and project_manager role.
        """
        return self.is_super_admin(chat_id) or self.get_role(chat_id) == "project_manager"

    def can_confirm_pm(self, chat_id: str) -> bool:
        """Check if a user can PM-confirm HR requests (gate 1)."""
        return self.get_role(chat_id) in self.CAN_CONFIRM_PM_ROLES

    def can_approve_hr(self, chat_id: str) -> bool:
        """Check if a user can HR-decide requests (gate 2, final)."""
        return self.get_role(chat_id) in self.CAN_APPROVE_HR_ROLES

    # --- Capability layer (008 tenancy) ---

    def has_capability(self, chat_id: str, capability: str,
                       site_id: str | None = None) -> bool:
        """Capability check: membership grants, else role defaults.

        Args:
            chat_id: Telegram chat ID.
            capability: Registry name (unknown names always deny).
            site_id: Tenant site (defaults to this bot's SITE_ID).

        Returns:
            True only with an active membership grant or a role default.
        """
        from app.auth import capabilities as caps
        from app.database import driver

        if not caps.is_known(capability):
            return False
        role = self.get_role(chat_id)
        if role in ("pending", "rejected"):
            return False
        site = site_id or driver.site_id()
        if self._memberships is not None:
            membership = self._memberships.find(chat_id, site)
            if membership is not None and membership.status == "active":
                if membership.capabilities:
                    return capability in membership.capabilities
                # No explicit grants: fall through to role defaults
            elif membership is not None:
                return False  # suspended
            else:
                return False  # no membership in this site
        return capability in caps.for_role(role)

    def resolve_active_site(self, chat_id: str,
                            session_site: str | None = None) -> str | None:
        """Validate session site against memberships; auto-bind singles.

        Returns:
            The authorized active site, or None when the user must pick
            (multi-site, no valid session) or has no membership at all.
        """
        if self._memberships is None:
            return session_site
        memberships = self._memberships.active_for_user(chat_id)
        sites = [m.site_id for m in memberships]
        if session_site in sites:
            return session_site
        if len(sites) == 1:
            return sites[0]
        return None

    def migrate_memberships(self) -> int:
        """Backfill memberships from legacy users.site_id. Returns rows created."""
        if self._memberships is None:
            return 0
        return self._memberships.migrate_from_users(self._repo)

    # --- User management ---

    def register_or_get(self, chat_id: str, username: Optional[str] = None,
                        first_name: Optional[str] = None) -> User:
        """Register a new user or return existing.

        Super admin is auto-registered with superadmin role on first visit.
        Configured admins are auto-registered with admin role on first visit.

        Args:
            chat_id: Telegram chat ID.
            username: Telegram username.
            first_name: Telegram first name.

        Returns:
            The User dataclass.
        """
        existing = self._repo.get_by_chat_id(chat_id)
        if existing:
            # Update profile info if changed
            if (username and username != existing.username) or \
               (first_name and first_name != existing.first_name):
                existing.username = username
                existing.first_name = first_name
                self._repo.upsert(existing)
            return existing

        # New user — determine initial role
        if chat_id == self._super_admin_chat_id:
            role = "superadmin"
        elif chat_id in self._extra_admin_ids:
            role = "admin"
        else:
            role = "pending"

        user = User(
            chat_id=chat_id,
            role=role,
            username=username,
            first_name=first_name,
        )
        result = self._repo.upsert(user)

        if role == "superadmin":
            logger.info("Super admin registered: %s (%s %s)", chat_id, first_name or "", username or "")
        elif role == "admin":
            logger.info("Admin auto-registered: %s (%s %s)", chat_id, first_name or "", username or "")
        else:
            logger.info(
                "New user registered (pending): %s (%s %s)",
                chat_id, first_name or "", username or "",
            )

        return result

    def approve_user(self, chat_id: str, approved_by: str, role: str = "normal_user") -> Optional[User]:
        """Approve a pending user, making them a normal_user or viewer.

        Args:
            chat_id: Chat ID of the user to approve.
            approved_by: Chat ID of the admin approving.
            role: Role to assign ('normal_user' or 'viewer').

        Returns:
            Updated User dataclass, or None if not found.
        """
        if role not in ("normal_user", "viewer"):
            role = "normal_user"
        updated = self._repo.set_role(chat_id, role, approved_by=approved_by)
        if updated is not None and self._memberships is not None:
            from app.database import driver

            site = (updated.site_id or "").strip() or driver.site_id()
            self._memberships.grant(chat_id, site, [])
        return updated

    def reject_user(self, chat_id: str, rejected_by: str) -> Optional[User]:
        """Reject a pending user.

        Args:
            chat_id: Chat ID of the user to reject.
            rejected_by: Chat ID of the admin rejecting.

        Returns:
            Updated User dataclass, or None if not found.
        """
        return self._repo.set_role(chat_id, "rejected", approved_by=rejected_by)

    def promote_to_admin(self, chat_id: str, promoted_by: str) -> Optional[User]:
        """Promote a user to admin.

        Args:
            chat_id: Chat ID of the user to promote.
            promoted_by: Chat ID of the super admin promoting.

        Returns:
            Updated User dataclass, or None if not found.
        """
        return self._repo.set_role(chat_id, "admin", approved_by=promoted_by)

    def demote_to_user(self, chat_id: str, demoted_by: str) -> Optional[User]:
        """Demote an admin to normal_user.

        Args:
            chat_id: Chat ID of the admin to demote.
            demoted_by: Chat ID of the super admin demoting.

        Returns:
            Updated User dataclass, or None if not found.
        """
        return self._repo.set_role(chat_id, "normal_user", approved_by=demoted_by)

    def set_role(self, chat_id: str, role: str, changed_by: str) -> Optional[User]:
        """Set a user's role directly.

        Args:
            chat_id: Chat ID of the user.
            role: New role to assign.
            changed_by: Chat ID of the admin changing the role.

        Returns:
            Updated User dataclass, or None if not found.
        """
        valid_roles = ("superadmin", "project_manager", "executive_engineer", "admin", "hr", "normal_user", "viewer", "pending", "rejected")
        if role not in valid_roles:
            logger.warning("Invalid role '%s' requested for user %s", role, chat_id)
            return None
        return self._repo.set_role(chat_id, role, approved_by=changed_by)

    def get_pending_users(self) -> list[User]:
        """Get all users awaiting approval.

        Returns:
            List of pending User dataclasses.
        """
        return self._repo.get_pending_users()

    def get_all_users(self) -> list[User]:
        """Get all registered users.

        Returns:
            List of all User dataclasses.
        """
        return self._repo.get_all_users()

    def get_user(self, chat_id: str) -> Optional[User]:
        """Get a user by chat ID.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            User dataclass or None.
        """
        return self._repo.get_by_chat_id(chat_id)

    def get_super_admin_chat_id(self) -> str:
        """Get the super admin chat ID.

        Returns:
            The super admin's chat ID string.
        """
        return self._super_admin_chat_id

    def get_all_approved_chat_ids(self) -> set[str]:
        """Get all approved (non-pending, non-rejected) user chat IDs.

        Returns:
            Set of chat ID strings for approved users.
        """
        approved = set()
        approved.add(self._super_admin_chat_id)
        approved.update(self._extra_admin_ids)
        all_users = self._repo.get_all_users()
        for u in all_users:
            if u.role not in ("pending", "rejected"):
                approved.add(u.chat_id)
        return approved

    def get_all_admin_chat_ids(self) -> set[str]:
        """Get all admin, executive_engineer, project_manager, and superadmin chat IDs.

        Returns:
            Set of chat ID strings.
        """
        admins = set()
        admins.add(self._super_admin_chat_id)
        admins.update(self._extra_admin_ids)
        for u in self._repo.get_users_by_role("admin"):
            admins.add(u.chat_id)
        for u in self._repo.get_users_by_role("project_manager"):
            admins.add(u.chat_id)
        for u in self._repo.get_users_by_role("executive_engineer"):
            admins.add(u.chat_id)
        return admins

    def get_users_by_role(self, role: str) -> list[User]:
        """Get all users with a specific role.

        Args:
            role: The role to filter by.

        Returns:
            List of User dataclasses with the given role.
        """
        return self._repo.get_users_by_role(role)
