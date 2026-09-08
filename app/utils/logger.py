"""
Centralized logging configuration for Labor-Report.

Provides a configured logger with:
- File handler with rotation
- Console handler with formatting
- Consistent format across all modules
- Thread-safe logging
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.models.config import LoggingConfig


def _create_formatter() -> logging.Formatter:
    """Create the standard log formatter for Labor-Report.

    Returns:
        A configured logging.Formatter instance.
    """
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _create_console_handler(formatter: logging.Formatter, level: str) -> logging.Handler:
    """Create a console (stdout) log handler.

    Args:
        formatter: The formatter to use.
        level: The log level.

    Returns:
        A configured StreamHandler.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _create_file_handler(log_path: Path, formatter: logging.Formatter, config: LoggingConfig) -> logging.Handler:
    """Create a rotating file log handler.

    Args:
        log_path: Path to the log file.
        formatter: The formatter to use.
        config: Logging configuration.

    Returns:
        A configured RotatingFileHandler.
    """
    # Ensure the log directory exists
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    handler.setLevel(config.level)
    handler.setFormatter(formatter)
    return handler


def setup_logger(config: Optional[LoggingConfig] = None, name: str = "labor_report") -> logging.Logger:
    """Set up and configure the application logger.

    Creates a logger with both file and console handlers.
    Can be called multiple times safely (handlers are only added once).

    Args:
        config: Logging configuration. If None, uses default settings.
        name: Logger name (typically the module name).

    Returns:
        Configured logger instance.

    Example:
        >>> logger = setup_logger()
        >>> logger.info("Application started")
    """
    if config is None:
        config = LoggingConfig()

    logger = logging.getLogger(name)

    # Avoid duplicate handlers if setup_logger is called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)  # Root level; handlers filter further
    logger.propagate = False  # Don't propagate to root logger

    formatter = _create_formatter()

    # Console handler
    console_handler = _create_console_handler(formatter, config.level)
    logger.addHandler(console_handler)

    # File handler
    log_path = Path(config.file)
    file_handler = _create_file_handler(log_path, formatter, config)
    logger.addHandler(file_handler)

    logger.debug("Logger initialized: level=%s, file=%s", config.level, log_path.resolve())

    return logger


def get_logger(name: str = "labor_report") -> logging.Logger:
    """Get a named logger.

    If the logger hasn't been set up yet, sets it up with default config.

    Args:
        name: Logger name (typically __name__ from the calling module).

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    # If not configured yet, set up with defaults
    if not logger.handlers:
        return setup_logger(name=name)

    return logger
