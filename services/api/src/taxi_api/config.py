"""Runtime configuration, read from ``TAXI_API_*`` environment variables."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _upper(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAXI_API_", extra="ignore")

    log_level: Annotated[LogLevel, BeforeValidator(_upper)] = "INFO"
    # MLflow model URI, e.g. "models:/taxi-trip-duration@champion". Wired up in Phase 5.
    model_uri: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings for the production entry point (``taxi_api.main:app``)."""
    return Settings()
