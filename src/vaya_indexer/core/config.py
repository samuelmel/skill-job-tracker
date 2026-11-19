"""Configuracao via variaveis de ambiente (ou arquivo .env)."""

from functools import lru_cache

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProcessingPolicy(BaseModel):
    """Limiares de deduplicacao e guardas de reconciliacao (valores de Settings)."""

    dedup_merge_threshold: float = Field(default=0.90, ge=0, le=1)
    dedup_review_threshold: float = Field(default=0.75, ge=0, le=1)
    dedup_candidate_limit: int = Field(default=200, ge=1)
    dedup_url_min_title_similarity: float = Field(default=0.6, ge=0, le=1)
    reconcile_max_close_ratio: float = Field(default=0.5, ge=0, le=1)
    reconcile_min_block_count: int = Field(default=5, ge=0)
    reconcile_misses_to_close: int = Field(default=2, ge=1)
    reconcile_grace_hours: float = Field(default=12.0, ge=0)

    @model_validator(mode="after")
    def _review_below_merge(self) -> "ProcessingPolicy":
        if self.dedup_review_threshold > self.dedup_merge_threshold:
            raise ValueError("dedup_review_threshold deve ser <= dedup_merge_threshold")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(description="URL SQLAlchemy, ex.: postgresql+psycopg://...")
    http_timeout: float = Field(default=20.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    user_agent: str = "VayaJobIndexer/0.1 (+https://example.invalid/contact)"
    per_domain_concurrency: int = Field(default=4, ge=1)
    per_domain_min_interval: float = Field(default=0.2, ge=0)
    log_level: str = "INFO"
    dedup_merge_threshold: float = Field(default=0.90, ge=0, le=1)
    dedup_review_threshold: float = Field(default=0.75, ge=0, le=1)
    dedup_candidate_limit: int = Field(default=200, ge=1)
    dedup_url_min_title_similarity: float = Field(default=0.6, ge=0, le=1)
    reconcile_max_close_ratio: float = Field(default=0.5, ge=0, le=1)
    reconcile_min_block_count: int = Field(default=5, ge=0)
    reconcile_misses_to_close: int = Field(default=2, ge=1)
    reconcile_grace_hours: float = Field(default=12.0, ge=0)

    def policy(self) -> ProcessingPolicy:
        return ProcessingPolicy(
            dedup_merge_threshold=self.dedup_merge_threshold,
            dedup_review_threshold=self.dedup_review_threshold,
            dedup_candidate_limit=self.dedup_candidate_limit,
            dedup_url_min_title_similarity=self.dedup_url_min_title_similarity,
            reconcile_max_close_ratio=self.reconcile_max_close_ratio,
            reconcile_min_block_count=self.reconcile_min_block_count,
            reconcile_misses_to_close=self.reconcile_misses_to_close,
            reconcile_grace_hours=self.reconcile_grace_hours,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
