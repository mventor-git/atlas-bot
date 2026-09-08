"""
Tests for the logger module.

Tests cover:
- Logger initialization
- File logging
- Console logging
- Log level filtering
- Log rotation preparation
- Handler configuration
"""

import logging
import tempfile
from pathlib import Path

import pytest

from app.models.config import LoggingConfig
from app.utils.logger import setup_logger, get_logger


class TestLogger:
    """Test suite for logger setup."""

    def test_setup_logger_returns_logger(self):
        """Should return a configured Logger instance."""
        logger = setup_logger(name="test_logger")
        assert isinstance(logger, logging.Logger), f"Expected Logger instance, got {type(logger)}"
        assert logger.name == "test_logger", f"Expected 'test_logger', got '{logger.name}'"

    def test_setup_logger_adds_handlers(self):
        """Should add handlers to the logger."""
        logger = setup_logger(name="test_handlers")
        handler_types = [type(h).__name__ for h in logger.handlers]
        assert "StreamHandler" in handler_types, f"Expected StreamHandler, got {handler_types}"
        assert "RotatingFileHandler" in handler_types, f"Expected RotatingFileHandler, got {handler_types}"

    def test_setup_logger_console_handler(self):
        """Should have a console (stdout) handler."""
        logger = setup_logger(name="test_console")
        handler_types = [type(h).__name__ for h in logger.handlers]
        assert "StreamHandler" in handler_types, f"Expected StreamHandler, got {handler_types}"

    def test_setup_logger_file_handler(self):
        """Should have a file handler."""
        logger = setup_logger(name="test_file")
        handler_types = [type(h).__name__ for h in logger.handlers]
        assert "RotatingFileHandler" in handler_types, f"Expected RotatingFileHandler, got {handler_types}"

    def test_setup_logger_no_duplicate_handlers(self):
        """Should not add duplicate handlers on repeated calls."""
        logger = setup_logger(name="test_no_dup")
        handler_count = len(logger.handlers)

        logger2 = setup_logger(name="test_no_dup")
        assert len(logger2.handlers) == handler_count, f"Expected {handler_count} handlers, got {len(logger2.handlers)}"

    def test_get_logger_returns_configured_logger(self):
        """Should return a usable logger via get_logger."""
        logger = get_logger("test_get_logger")
        assert isinstance(logger, logging.Logger), f"Expected Logger instance, got {type(logger)}"
        # Should not raise
        logger.debug("Test debug message")
        logger.info("Test info message")

    def _cleanup_logger(self, logger_name: str) -> None:
        """Close and remove all handlers for a logger to release file locks."""
        logger = logging.getLogger(logger_name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    def test_logger_writes_to_file(self):
        """Should write log messages to the log file."""
        tmp_dir_obj = tempfile.TemporaryDirectory()
        try:
            tmp_dir = tmp_dir_obj.name
            log_file = Path(tmp_dir) / "test.log"
            config = LoggingConfig(file=str(log_file), level="DEBUG")

            logger = setup_logger(config=config, name="test_file_write")
            logger.info("This is a test message")

            # Close handlers to release file lock
            self._cleanup_logger("test_file_write")

            # Verify file was written
            assert log_file.exists(), f"Log file {log_file} was not created"
            content = log_file.read_text(encoding="utf-8")
            assert "This is a test message" in content, f"Expected test message in log, got: {content[:200]}"
        finally:
            tmp_dir_obj.cleanup()

    def test_logger_respects_level(self):
        """Should filter messages below the configured level."""
        tmp_dir_obj = tempfile.TemporaryDirectory()
        try:
            tmp_dir = tmp_dir_obj.name
            log_file = Path(tmp_dir) / "test_level.log"
            config = LoggingConfig(file=str(log_file), level="WARNING")

            logger = setup_logger(config=config, name="test_level")

            # These should NOT appear in the log
            logger.debug("Debug message")
            logger.info("Info message")

            # This SHOULD appear
            logger.warning("Warning message")
            logger.error("Error message")

            # Close handlers to release file lock
            self._cleanup_logger("test_level")

            content = log_file.read_text(encoding="utf-8")
            assert "Warning message" in content, f"Expected 'Warning message' in log: {content}"
            assert "Error message" in content, f"Expected 'Error message' in log: {content}"
            assert "Debug message" not in content, f"Unexpected 'Debug message' in log: {content}"
            assert "Info message" not in content, f"Unexpected 'Info message' in log: {content}"
        finally:
            tmp_dir_obj.cleanup()

    def test_logger_uses_correct_format(self):
        """Should use the standard log format."""
        tmp_dir_obj = tempfile.TemporaryDirectory()
        try:
            tmp_dir = tmp_dir_obj.name
            log_file = Path(tmp_dir) / "test_format.log"
            config = LoggingConfig(file=str(log_file), level="INFO")

            logger = setup_logger(config=config, name="test_format")
            logger.info("Format test")

            # Close handlers to release file lock
            self._cleanup_logger("test_format")

            content = log_file.read_text(encoding="utf-8")
            # Format: "2024-01-01 12:00:00 | INFO     | test_format | Format test"
            import re
            assert re.search(
                r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| INFO\s+ \| test_format \| Format test",
                content,
            ), f"Log format mismatch. Content: {content[:200]}"
        finally:
            tmp_dir_obj.cleanup()

    def test_multiple_loggers_independent(self):
        """Should allow multiple independent loggers."""
        logger1 = setup_logger(name="logger_one")
        logger2 = setup_logger(name="logger_two")

        assert logger1 is not logger2, f"Expected different logger instances"
        assert logger1.name == "logger_one", f"Expected 'logger_one', got '{logger1.name}'"
        assert logger2.name == "logger_two", f"Expected 'logger_two', got '{logger2.name}'"

    def test_setup_logger_with_empty_name(self):
        """Should handle empty logger name (root logger)."""
        logger = setup_logger(name="")
        assert logger.name == "root", f"Expected root logger, got '{logger.name}'"
        # Clean up handlers to avoid polluting other tests
        for h in logger.handlers[:]:
            logger.removeHandler(h)
            h.close()
