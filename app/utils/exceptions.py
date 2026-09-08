"""
Custom exceptions for Labor-Report.

Defines a hierarchy of application-specific exceptions
for clean error handling and logging.
"""


class LaborReportError(Exception):
    """Base exception for all Labor-Report errors."""

    def __init__(self, message: str, original_exception: Exception | None = None) -> None:
        """Initialize the exception.

        Args:
            message: Human-readable error description.
            original_exception: The original exception that caused this error (if any).
        """
        self.original_exception = original_exception
        super().__init__(message)

    @property
    def user_message(self) -> str:
        """Get a user-friendly error message for Telegram responses."""
        return "An unexpected error occurred. Please try again later."


class ConfigurationError(LaborReportError):
    """Raised when configuration loading or validation fails."""

    @property
    def user_message(self) -> str:
        return "System configuration error. Please contact the administrator."


class TemplateError(LaborReportError):
    """Raised when there is an issue with the Excel template."""

    @property
    def user_message(self) -> str:
        return "There was an error processing the report template. Please contact the administrator."


class DatabaseError(LaborReportError):
    """Raised when a database operation fails."""

    @property
    def user_message(self) -> str:
        return "A database error occurred. Please try again."


class ExcelError(LaborReportError):
    """Raised when Excel manipulation fails."""

    @property
    def user_message(self) -> str:
        return "An error occurred while generating the Excel file. Please try again."


class PDFError(LaborReportError):
    """Raised when PDF generation fails."""

    @property
    def user_message(self) -> str:
        return "An error occurred while generating the PDF. Please ensure Excel is installed."


class BotError(LaborReportError):
    """Raised when Telegram bot operations fail."""

    @property
    def user_message(self) -> str:
        return "A communication error occurred. Please try again."


class ValidationError(LaborReportError):
    """Raised when user input validation fails."""

    def __init__(self, message: str, field: str | None = None) -> None:
        """Initialize validation error.

        Args:
            message: Validation error description.
            field: The field that failed validation.
        """
        self.field = field
        super().__init__(message)

    @property
    def user_message(self) -> str:
        return str(self.args[0]) if self.args else "Invalid input. Please try again."


class ReportLifecycleError(LaborReportError):
    """Raised when an invalid report lifecycle transition is attempted. (NEW v2.0)

    For example:
    - Editing a finalized or locked report
    - Transitioning from Final back to Draft without admin unlock
    - Transitioning from Locked to any non-Draft state
    """

    def __init__(
        self,
        message: str,
        current_status: str | None = None,
        target_status: str | None = None,
    ) -> None:
        """Initialize lifecycle error.

        Args:
            message: Description of the invalid transition.
            current_status: The current report status.
            target_status: The attempted target status.
        """
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(message)

    @property
    def user_message(self) -> str:
        msg = str(self.args[0]) if self.args else None
        if msg:
            return msg
        if self.current_status and self.target_status:
            return f"Cannot change report from '{self.current_status}' to '{self.target_status}'."
        return "This action is not allowed."
