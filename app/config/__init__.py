"""Configuration module for Labor-Report.

Provides configuration loading, validation, and access.
"""

from app.config.loader import ConfigLoader, ConfigurationError

__all__ = [
    "ConfigLoader",
    "ConfigurationError",
]
