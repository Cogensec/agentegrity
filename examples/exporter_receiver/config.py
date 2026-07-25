"""Configuration for the reference SessionExporter HTTP receiver."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    posthog_project_token: str = "<ph_project_token>"
    posthog_host: str = "https://us.i.posthog.com"
    posthog_disabled: bool = False
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
