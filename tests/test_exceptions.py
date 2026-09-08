"""
Tests for custom exception classes.
"""

import pytest

from app.utils.exceptions import (
    LaborReportError,
    TemplateError,
    DatabaseError,
    ExcelError,
    PDFError,
    BotError,
    ValidationError,
)


class TestLaborReportError:
    """Test suite for base exception."""

    def test_base_exception_message(self):
        """Should store error message."""
        error = LaborReportError("Test error")
        assert str(error) == "Test error", (
            f"Expected 'Test error', got '{str(error)}'"
        )

    def test_base_exception_with_original(self):
        """Should store the original exception."""
        original = ValueError("Original error")
        error = LaborReportError("Wrapped error", original)
        assert error.original_exception is original, (
            "original_exception does not match the original exception"
        )

    def test_base_exception_user_message(self):
        """Should provide a default user-friendly message."""
        error = LaborReportError("Test")
        assert "unexpected error" in error.user_message.lower(), (
            f"Expected 'unexpected error' in user_message, "
            f"got '{error.user_message}'"
        )

    def test_base_exception_without_original(self):
        """Should handle missing original exception."""
        error = LaborReportError("Test error")
        assert error.original_exception is None, (
            "Expected original_exception=None when not provided"
        )

    def test_exception_with_empty_message(self):
        """Should handle empty message string."""
        error = LaborReportError("")
        assert str(error) == "", (
            f"Expected empty string, got '{str(error)}'"
        )


class TestSpecificExceptions:
    """Test suite for specific exception types."""

    def test_template_error_user_message(self):
        """Should provide template-specific user message."""
        error = TemplateError("Template issue")
        assert "template" in error.user_message.lower(), (
            f"Expected 'template' in user_message, got '{error.user_message}'"
        )

    def test_database_error_user_message(self):
        """Should provide database-specific user message."""
        error = DatabaseError("DB issue")
        assert "database" in error.user_message.lower(), (
            f"Expected 'database' in user_message, got '{error.user_message}'"
        )

    def test_excel_error_user_message(self):
        """Should provide Excel-specific user message."""
        error = ExcelError("Excel issue")
        assert "excel" in error.user_message.lower(), (
            f"Expected 'excel' in user_message, got '{error.user_message}'"
        )

    def test_pdf_error_user_message(self):
        """Should provide PDF-specific user message."""
        error = PDFError("PDF issue")
        assert "excel is installed" in error.user_message.lower(), (
            f"Expected 'excel is installed' in user_message, "
            f"got '{error.user_message}'"
        )

    def test_bot_error_user_message(self):
        """Should provide bot-specific user message."""
        error = BotError("Bot issue")
        assert "communication" in error.user_message.lower(), (
            f"Expected 'communication' in user_message, "
            f"got '{error.user_message}'"
        )

    def test_validation_error_message(self):
        """Should store validation error message."""
        error = ValidationError("Invalid input")
        assert str(error) == "Invalid input", (
            f"Expected 'Invalid input', got '{str(error)}'"
        )

    def test_validation_error_with_field(self):
        """Should store the field name for validation errors."""
        error = ValidationError("Invalid workers count", field="workers")
        assert error.field == "workers", (
            f"Expected field='workers', got '{error.field}'"
        )
        assert "Invalid workers count" == error.user_message, (
            f"Expected user_message='Invalid workers count', "
            f"got '{error.user_message}'"
        )

    def test_validation_error_user_message(self):
        """Should use the validation message as user message."""
        error = ValidationError("Please enter a valid number")
        assert error.user_message == "Please enter a valid number", (
            f"Expected user_message='Please enter a valid number', "
            f"got '{error.user_message}'"
        )

    def test_exception_inheritance(self):
        """Should maintain proper exception hierarchy."""
        assert issubclass(TemplateError, LaborReportError), (
            "TemplateError is not a subclass of LaborReportError"
        )
        assert issubclass(DatabaseError, LaborReportError), (
            "DatabaseError is not a subclass of LaborReportError"
        )
        assert issubclass(ExcelError, LaborReportError), (
            "ExcelError is not a subclass of LaborReportError"
        )
        assert issubclass(PDFError, LaborReportError), (
            "PDFError is not a subclass of LaborReportError"
        )
        assert issubclass(BotError, LaborReportError), (
            "BotError is not a subclass of LaborReportError"
        )
        assert issubclass(ValidationError, LaborReportError), (
            "ValidationError is not a subclass of LaborReportError"
        )
