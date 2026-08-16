"""Ignore local dotenv defaults while preserving explicit test environment overrides."""

from app.config import Settings, get_settings

Settings.model_config["env_file"] = None
get_settings.cache_clear()
