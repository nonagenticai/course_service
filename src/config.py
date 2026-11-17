"""Configuration management for course_service using Pydantic Settings.

Hierarchical settings with sub-configs for all course_service components.
"""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from w2v_common.config import (
    APISettings,
    AuthSettings,
    DatabaseSettings,
    LoggingSettings,
    RedisSettings,
    StorageSettings,
)


class CourseSettings(BaseSettings):
    """Course generation configuration.

    LLM provider config (model, base_url, api_key) deliberately lives nowhere
    in this service — every LLM call goes through worker_management's
    ``POST /workers/{name}/message`` endpoint, which owns provider routing,
    keys, and per-tenant cost attribution. WM is reached via
    ``WORKER_MANAGEMENT_URL`` (env, picked up by ``WMClient``); per-request
    model overrides flow through ``CourseConfig.llm_model`` from the SPA.
    """

    default_concurrency_limit: int = Field(
        default=10, description="Default concurrent lesson pipelines"
    )
    default_max_retries: int = Field(default=3, description="Default max retries per lesson")
    default_max_lessons: int = Field(default=200, description="Maximum lessons per course")
    default_lesson_duration: int = Field(
        default=120, description="Default target lesson duration in seconds"
    )
    callback_base_url: str = Field(
        default="", description="Base URL for orchestrator webhook callbacks"
    )

    model_config = SettingsConfigDict(
        env_prefix="COURSE_", env_file=".env", case_sensitive=False, extra="ignore"
    )


# ---------------------------------------------------------------------------
# Root Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Root settings aggregating all sub-configurations for course_service."""

    app_name: str = Field(default="course_service", description="Application name")
    app_version: str = Field(default="0.1.0", description="Application version")
    environment: str = Field(
        default="development", description="Environment (development, staging, production)"
    )

    # Shared infrastructure
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    api: APISettings = Field(default_factory=APISettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    # Course-domain settings
    course: CourseSettings = Field(default_factory=CourseSettings)

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @model_validator(mode="after")
    def validate_settings(self) -> Settings:
        """Validate cross-field constraints."""
        if self.api.port == 8000:
            raise ValueError("API port 8000 not allowed. Default: 6314.")
        return self

    def get_database_url(self) -> str:
        """Return the configured PostgreSQL database URL."""
        return self.database.url

    def get_redis_url(self) -> str:
        """Return the configured Redis connection URL."""
        return self.redis.url

    def is_production(self) -> bool:
        """Return True if the environment is production."""
        return self.environment.lower() == "production"

    def is_development(self) -> bool:
        """Return True if the environment is development."""
        return self.environment.lower() == "development"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear the cached settings instance (useful for testing)."""
    get_settings.cache_clear()
