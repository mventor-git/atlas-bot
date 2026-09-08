"""
Configuration loader for Labor-Report.

Loads configuration from config.yaml and environment variables.
Supports path resolution, validation, and singleton access pattern.
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from app.models.config import AppConfig


class ConfigurationError(Exception):
    """Raised when configuration loading or validation fails."""


class ConfigLoader:
    """Loads and provides access to application configuration.

    Loads config.yaml on initialization, validates it with Pydantic,
    and provides the AppConfig object for dependency injection.

    Usage:
        config = ConfigLoader.load()
        template_path = config.template_path
    """

    _instance: Optional["ConfigLoader"] = None
    _config: Optional[AppConfig] = None

    # Default config path relative to project root
    DEFAULT_CONFIG_PATH = "config/config.yaml"

    def __init__(self, config_path: Optional[str] = None) -> None:
        """Initialize the ConfigLoader.

        Args:
            config_path: Path to config.yaml. If None, uses default.

        Raises:
            ConfigurationError: If config file is missing or invalid.
        """
        self._config_path = Path(config_path or self.DEFAULT_CONFIG_PATH)
        load_dotenv()  # Load .env file for environment variables
        self._config = self._load_and_validate()

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> AppConfig:
        """Load configuration (singleton pattern).

        Args:
            config_path: Optional path to config.yaml.

        Returns:
            AppConfig instance with validated settings.

        Raises:
            ConfigurationError: If configuration fails.
        """
        if cls._instance is None or config_path is not None:
            cls._instance = cls(config_path)
        return cls._instance._config  # type: ignore[return-value]

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
        cls._config = None

    def _load_and_validate(self) -> AppConfig:
        """Load YAML config file and validate with Pydantic.

        Returns:
            Validated AppConfig instance.

        Raises:
            ConfigurationError: If file is missing, invalid YAML, or validation fails.
        """
        config_file = self._resolve_path(self._config_path)

        if not config_file.exists():
            raise ConfigurationError(
                f"Configuration file not found: {config_file}\n"
                f"Please ensure config.yaml exists at: {config_file}"
            )

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigurationError(
                f"Failed to parse configuration file: {config_file}\n"
                f"YAML error: {e}"
            ) from e

        if raw_config is None:
            raise ConfigurationError(
                f"Configuration file is empty: {config_file}"
            )

        try:
            return AppConfig(**raw_config)
        except Exception as e:
            raise ConfigurationError(
                f"Configuration validation failed: {e}"
            ) from e

    @staticmethod
    def _resolve_path(path: Path) -> Path:
        """Resolve a path relative to the project root.

        First tries the path as-is. If it doesn't exist,
        tries to find it relative to the project root.

        Args:
            path: Path to resolve.

        Returns:
            Resolved absolute Path.
        """
        if path.exists():
            return path.resolve()

        # Try common project root locations
        project_roots = [
            Path.cwd(),
            Path(__file__).parent.parent.parent,  # app/config/ -> Labor-Report/
        ]

        for root in project_roots:
            candidate = (root / path).resolve()
            if candidate.exists():
                return candidate

        # Return the original path (will fail with a clear error later)
        return path.resolve()

    @staticmethod
    def get_bot_token() -> str:
        """Get the Telegram bot token from environment.

        Returns:
            Bot token string.

        Raises:
            ConfigurationError: If BOT_TOKEN environment variable is not set.
        """
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise ConfigurationError(
                "BOT_TOKEN environment variable is not set.\n"
                "Create a .env file with BOT_TOKEN=your_token_here\n"
                "or set it as an environment variable."
            )
        return token
