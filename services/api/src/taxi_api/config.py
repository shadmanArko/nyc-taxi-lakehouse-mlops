"""Runtime configuration, read from ``TAXI_API_*`` environment variables."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _upper(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAXI_API_", extra="ignore")

    log_level: Annotated[LogLevel, BeforeValidator(_upper)] = "INFO"
    # Registry alias to serve, e.g. "models:/taxi-trip-duration@champion". The MLflow server
    # comes from the standard MLFLOW_TRACKING_URI variable.
    model_uri: str | None = None
    # How often to check whether the alias moved to a new version (hot reload).
    model_refresh_seconds: Annotated[int, Field(ge=5, le=3600)] = 60


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings for the production entry point (``taxi_api.main:app``)."""
    return Settings()
