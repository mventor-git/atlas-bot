"""
Session Manager for Labor-Report.

Manages per-user conversation sessions for the Telegram bot workflow.
Provides thread-safe session storage with automatic timeout cleanup.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from app.models.config import AppConfig
from app.models.database import SessionState, UserSession
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SessionManagerError(Exception):
    """Raised when a session operation fails."""


class SessionManager:
    """Manages per-user conversation sessions with thread safety and timeout cleanup.

    Stores sessions in an in-memory dictionary with a threading lock for
    concurrent access from multiple Telegram update handlers. Expired sessions
    are cleaned up by a background thread.

    Usage:
        manager = SessionManager(config)
        session = manager.create_session("user123")
        manager.update_state("user123", SessionState.AWAITING_DATE)
        manager.set_data("user123", "date", "2026-07-11")
        # ... later ...
        manager.stop()  # Stop the cleanup thread
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize the session manager.

        Args:
            config: Application configuration.
        """
        self._config = config
        self._timeout_minutes = config.session.timeout_minutes
        self._cleanup_interval = config.session.cleanup_interval_seconds

        self._sessions: dict[str, UserSession] = {}
        self._lock = threading.Lock()
        self._running = True
        self._stop_event = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="session-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()
        logger.info(
            "SessionManager initialized: timeout=%dmin, cleanup_interval=%ds",
            self._timeout_minutes, self._cleanup_interval,
        )

    # --- Session CRUD ---

    def create_session(self, telegram_user: str) -> UserSession:
        """Create a new session for a user.

        If a session already exists for this user, it is returned instead
        (no duplicate sessions).

        Args:
            telegram_user: Telegram user identifier.

        Returns:
            The created (or existing) UserSession.
        """
        with self._lock:
            existing = self._sessions.get(telegram_user)
            if existing is not None:
                logger.debug("Returning existing session for user %s", telegram_user)
                return existing

            session = UserSession(telegram_user=telegram_user, id=telegram_user)
            self._sessions[telegram_user] = session
            logger.debug("Created session for user %s (total: %d)", telegram_user, len(self._sessions))
            return session

    def get_session(self, telegram_user: str) -> Optional[UserSession]:
        """Get the current session for a user.

        Args:
            telegram_user: Telegram user identifier.

        Returns:
            The UserSession if it exists and has not timed out, None otherwise.
        """
        with self._lock:
            session = self._sessions.get(telegram_user)
            if session is None:
                return None

            # Check for timeout
            if self._is_expired(session):
                del self._sessions[telegram_user]
                logger.debug("Removed expired session for user %s", telegram_user)
                return None

            return session

    def get_or_create_session(self, telegram_user: str) -> UserSession:
        """Get existing session or create a new one.

        Args:
            telegram_user: Telegram user identifier.

        Returns:
            The UserSession (existing, newly created, or re-created if expired).
        """
        session = self.get_session(telegram_user)
        if session is not None:
            return session
        return self.create_session(telegram_user)

    def update_state(self, telegram_user: str, state: SessionState) -> UserSession:
        """Update the conversation state for a user.

        Args:
            telegram_user: Telegram user identifier.
            state: The new conversation state.

        Returns:
            The updated UserSession.

        Raises:
            SessionManagerError: If no session exists for the user.
        """
        with self._lock:
            session = self._sessions.get(telegram_user)
            if session is None:
                raise SessionManagerError(
                    f"No session found for user {telegram_user}. "
                    f"Call create_session() first."
                )

            session.state = state
            session.updated_at = datetime.now().isoformat()
            return session

    def set_data(self, telegram_user: str, key: str, value: object) -> UserSession:
        """Set a single data value in the user's session.

        Args:
            telegram_user: Telegram user identifier.
            key: Data key.
            value: Data value.

        Returns:
            The updated UserSession.

        Raises:
            SessionManagerError: If no session exists for the user.
        """
        with self._lock:
            session = self._sessions.get(telegram_user)
            if session is None:
                raise SessionManagerError(
                    f"No session found for user {telegram_user}."
                )

            session.data[key] = value
            session.updated_at = datetime.now().isoformat()
            return session

    def update_session_data(self, telegram_user: str, data: dict) -> UserSession:
        """Merge a dictionary of data into the user's session.

        Args:
            telegram_user: Telegram user identifier.
            data: Dictionary of data to merge.

        Returns:
            The updated UserSession.

        Raises:
            SessionManagerError: If no session exists for the user.
        """
        with self._lock:
            session = self._sessions.get(telegram_user)
            if session is None:
                raise SessionManagerError(
                    f"No session found for user {telegram_user}."
                )

            session.data.update(data)
            session.updated_at = datetime.now().isoformat()
            return session

    def clear_session(self, telegram_user: str) -> bool:
        """Remove a user's session.

        Args:
            telegram_user: Telegram user identifier.

        Returns:
            True if a session was removed, False if none existed.
        """
        with self._lock:
            if telegram_user in self._sessions:
                del self._sessions[telegram_user]
                logger.debug("Cleared session for user %s", telegram_user)
                return True
            return False

    def session_exists(self, telegram_user: str) -> bool:
        """Check if a session exists and has not timed out.

        Args:
            telegram_user: Telegram user identifier.

        Returns:
            True if the session exists and is valid.
        """
        return self.get_session(telegram_user) is not None

    # --- Statistics ---

    def get_active_session_count(self) -> int:
        """Get the number of currently active (non-expired) sessions.

        Returns:
            Count of active sessions.
        """
        with self._lock:
            now = datetime.now()
            count = 0
            expired_ids: list[str] = []
            for uid, session in self._sessions.items():
                if self._is_expired(session, now=now):
                    expired_ids.append(uid)
                else:
                    count += 1
            # Clean up expired sessions during count
            for uid in expired_ids:
                del self._sessions[uid]
            return count

    def get_all_sessions(self) -> list[UserSession]:
        """Get all non-expired sessions.

        Returns:
            List of active UserSession objects.
        """
        with self._lock:
            now = datetime.now()
            active: list[UserSession] = []
            expired_ids: list[str] = []
            for uid, session in self._sessions.items():
                if self._is_expired(session, now=now):
                    expired_ids.append(uid)
                else:
                    active.append(session)
            for uid in expired_ids:
                del self._sessions[uid]
            return active

    # --- Cleanup ---

    def cleanup_expired_sessions(self) -> int:
        """Remove all expired sessions.

        Returns:
            Number of sessions removed.
        """
        with self._lock:
            now = datetime.now()
            expired_ids = [
                uid for uid, session in self._sessions.items()
                if self._is_expired(session, now=now)
            ]
            for uid in expired_ids:
                del self._sessions[uid]
            count = len(expired_ids)
            if count > 0:
                logger.info("Cleaned up %d expired sessions", count)
            return count

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the background cleanup thread.

        Args:
            timeout: Seconds to wait for the thread to finish.
        """
        self._running = False
        self._stop_event.set()
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=timeout)
            if self._cleanup_thread.is_alive():
                logger.warning("Session cleanup thread did not stop within %ds", timeout)

    # --- Private ---

    def _is_expired(self, session: UserSession, now: Optional[datetime] = None) -> bool:
        """Check if a session has timed out.

        Args:
            session: The session to check.
            now: Current datetime (or None for auto).

        Returns:
            True if the session has expired.
        """
        if now is None:
            now = datetime.now()
        try:
            updated = datetime.fromisoformat(session.updated_at)
            return (now - updated) > timedelta(minutes=self._timeout_minutes)
        except (ValueError, TypeError):
            # If timestamp is malformed, treat as expired
            return True

    def _cleanup_loop(self) -> None:
        """Background thread that periodically removes expired sessions."""
        logger.debug("Session cleanup thread started (interval: %ds)", self._cleanup_interval)
        while self._running and not self._stop_event.is_set():
            try:
                self.cleanup_expired_sessions()
            except Exception as e:
                logger.warning("Error in session cleanup: %s", e)
            # Wait for the interval or until stop is called
            self._stop_event.wait(timeout=self._cleanup_interval)
        logger.debug("Session cleanup thread stopped")
