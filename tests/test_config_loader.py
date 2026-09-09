"""
Tests for the configuration loader module.

Tests cover:
- Loading valid configuration
- Handling missing files
- Handling invalid YAML
- Handling missing fields
- Singleton behavior
- Environment variable integration
"""

import os
from pathlib import Path

import pytest
import yaml
import pydantic

from app.config.loader import ConfigLoader, ConfigurationError
from app.models.config import AppConfig


class TestConfigLoader:
    """Test suite for ConfigLoader."""

    def test_load_valid_config(self, temp_config_file: Path):
        """Should load and validate a valid configuration file."""
        config = ConfigLoader.load(str(temp_config_file))
        assert isinstance(config, AppConfig), f"Expected AppConfig instance, got {type(config).__name__}"
        assert config.template.file == "templates/report.ots", f"Expected templates/report.ots, got {config.template.file}"
        assert config.date_cell == "B4", f"Expected B4, got {config.date_cell}"
        assert config.day_cell == "D4", f"Expected D4, got {config.day_cell}"
        assert config.table.start_row == 12, f"Expected 12, got {config.table.start_row}"
        assert config.table.columns.serial == "A", f"Expected A, got {config.table.columns.serial}"
        assert config.table.columns.contractor == "B", f"Expected B, got {config.table.columns.contractor}"
        assert config.logging.level == "INFO", f"Expected INFO, got {config.logging.level}"

    def test_load_missing_file_raises_error(self):
        """Should raise ConfigurationError for missing config file."""
        with pytest.raises(ConfigurationError, match="Configuration file not found"):
            ConfigLoader.load("nonexistent_config.yaml")

    def test_load_invalid_yaml_raises_error(self):
        """Should raise ConfigurationError for invalid YAML."""
        temp_file = Path("test_invalid_config.yaml")
        try:
            temp_file.write_text("invalid: [yaml: broken", encoding="utf-8")
            with pytest.raises(ConfigurationError, match="Failed to parse configuration"):
                ConfigLoader.load(str(temp_file))
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def test_load_empty_config_raises_error(self):
        """Should raise ConfigurationError for empty config file."""
        temp_file = Path("test_empty_config.yaml")
        try:
            temp_file.write_text("", encoding="utf-8")
            with pytest.raises(ConfigurationError, match="Configuration file is empty"):
                ConfigLoader.load(str(temp_file))
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def test_singleton_behavior(self, temp_config_file: Path):
        """Should return the same instance on repeated calls."""
        config1 = ConfigLoader.load(str(temp_config_file))
        config2 = ConfigLoader.load()
        assert config1 is config2, "Expected same instance from singleton"

    def test_singleton_reset(self, temp_config_file: Path):
        """Should allow resetting the singleton."""
        ConfigLoader.load(str(temp_config_file))
        ConfigLoader.reset()
        assert ConfigLoader._instance is None, "Expected _instance to be None after reset"
        assert ConfigLoader._config is None, "Expected _config to be None after reset"

    def test_load_without_optional_fields(self, temp_config_file: Path):
        """Should use defaults for optional fields."""
        # Create config with minimal fields
        minimal_config = {
            "template": {"file": "templates/test.xlsx", "tables_file": "database/tables.xlsx"},
        }
        temp_file = Path("test_minimal_config.yaml")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                yaml.dump(minimal_config, f)

            config = ConfigLoader.load(str(temp_file))
            assert config.date_cell == "B7", f"Expected default B7, got {config.date_cell}"
            assert config.day_cell == "B5", f"Expected default B5, got {config.day_cell}"
            assert config.table.start_row == 11, f"Expected default 11, got {config.table.start_row}"
            assert config.logging.level == "INFO", f"Expected default INFO, got {config.logging.level}"
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def test_bot_token_from_env(self, temp_config_file: Path):
        """Should read BOT_TOKEN from environment."""
        test_token = "test_bot_token_12345"
        os.environ["BOT_TOKEN"] = test_token
        try:
            token = ConfigLoader.get_bot_token()
            assert token == test_token, f"Expected {test_token}, got {token}"
        finally:
            del os.environ["BOT_TOKEN"]

    def test_bot_token_missing_raises_error(self):
        """Should raise ConfigurationError when BOT_TOKEN is missing."""
        # Ensure token is not in environment
        if "BOT_TOKEN" in os.environ:
            del os.environ["BOT_TOKEN"]

        with pytest.raises(ConfigurationError, match="BOT_TOKEN environment variable is not set"):
            ConfigLoader.get_bot_token()

    def test_load_empty_config_uses_defaults(self):
        """Should use defaults for empty config."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("template:\n  file: test.xlsx\n  tables_file: test.xlsx\n")
            temp_path = Path(f.name)
        try:
            ConfigLoader.reset()
            config = ConfigLoader.load(temp_path)
            assert config.template.file == "test.xlsx", f"Expected test.xlsx, got {config.template.file}"
            # Check defaults are applied
            assert config.database.path == "database/labor_reports.db", f"Expected default DB path"
        finally:
            if temp_path.exists():
                temp_path.unlink()
        ConfigLoader.reset()

    def test_path_properties(self, temp_config_file: Path):
        """Should resolve path properties correctly."""
        config = ConfigLoader.load(str(temp_config_file))
        assert isinstance(config.template_path, Path), f"Expected Path, got {type(config.template_path).__name__}"
        assert isinstance(config.pdf_folder_path, Path), f"Expected Path, got {type(config.pdf_folder_path).__name__}"
        assert isinstance(config.docs_folder_path, Path), f"Expected Path, got {type(config.docs_folder_path).__name__}"
        assert isinstance(config.database_path, Path), f"Expected Path, got {type(config.database_path).__name__}"


class TestAppConfigModel:
    """Test suite for AppConfig Pydantic model."""

    def test_valid_config_creation(self, sample_config_dict: dict):
        """Should create AppConfig from valid dict."""
        config = AppConfig(**sample_config_dict)
        assert config.date_cell == "B4", f"Expected B4, got {config.date_cell}"
        assert config.day_cell == "D4", f"Expected D4, got {config.day_cell}"

    def test_invalid_log_level_raises_error(self, sample_config_dict: dict):
        """Should validate log level."""
        sample_config_dict["logging"]["level"] = "INVALID"
        with pytest.raises(pydantic.ValidationError):
            AppConfig(**sample_config_dict)

    def test_negative_log_max_bytes_raises_error(self, sample_config_dict: dict):
        """Should validate log max_bytes."""
        sample_config_dict["logging"]["max_bytes"] = -1
        with pytest.raises(pydantic.ValidationError):
            AppConfig(**sample_config_dict)

    def test_backup_count_zero_is_valid(self):
        """Should accept backup_count of 0."""
        config = AppConfig(logging={"file": "log.txt", "level": "INFO", "max_bytes": 10240, "backup_count": 0})
        assert config.logging.backup_count == 0

    def test_max_bytes_at_boundary(self):
        """Should accept max_bytes at minimum (1024)."""
        config = AppConfig(logging={"file": "log.txt", "level": "DEBUG", "max_bytes": 1024, "backup_count": 1})
        assert config.logging.max_bytes == 1024, f"Expected 1024, got {config.logging.max_bytes}"
