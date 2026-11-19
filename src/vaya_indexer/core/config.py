"""Configuracao via variaveis de ambiente (ou arquivo .env)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(description="URL SQLAlchemy, ex.: postgresql+psycopg://...")
    http_timeout: float = Field(default=20.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    user_agent: str = "VayaJobIndexer/0.1 (+https://example.invalid/contact)"
    per_domain_concurrency: int = Field(default=4, ge=1)
    per_domain_min_interval: float = Field(default=0.2, ge=0)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
