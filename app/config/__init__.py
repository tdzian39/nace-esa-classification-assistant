"""Application settings (pydantic-settings). Connection details always come from environment variables."""

from config.settings import APP_ROOT, Settings, get_settings

__all__ = ["APP_ROOT", "Settings", "get_settings"]
