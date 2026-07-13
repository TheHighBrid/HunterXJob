from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApplicationMode(StrEnum):
    REVIEW = "review"
    DRY_RUN = "dry_run"
    AUTONOMOUS = "autonomous"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "HunterXJob v2"
    host: str = "127.0.0.1"
    port: int = 8011
    data_dir: Path = Path("./data")
    database_path: Path = Path("./data/hunterxjob-v2.db")
    log_dir: Path = Path("./logs")

    application_mode: ApplicationMode = ApplicationMode.DRY_RUN
    max_applications_per_day: int = Field(default=5, ge=1, le=100)
    minimum_match_score: float = Field(default=70.0, ge=0, le=100)

    ai_provider: str = "ollama"
    ai_base_url: str = "http://127.0.0.1:11434"
    ai_fast_model: str = "llama3.2:1b"
    ai_quality_model: str = "llama3.2:3b"
    ai_connect_timeout_seconds: float = Field(default=10.0, ge=1)
    ai_generation_timeout_seconds: float = Field(default=1200.0, ge=30)
    ai_max_retries: int = Field(default=2, ge=0, le=5)
    ai_keep_alive: str = "60m"

    browser_enabled: bool = True
    browser_headless: bool = True
    browser_timeout_seconds: float = Field(default=120.0, ge=10)

    scheduler_enabled: bool = False
    job_search_interval_minutes: int = Field(default=60, ge=15)
    automation_interval_minutes: int = Field(default=15, ge=15)

    target_locations: str = "Ottawa,Gatineau,National Capital Region,Remote Canada,Canada"
    target_keywords: str = "fraud,disputes,AML,KYC,compliance,collections,credit,bilingual,account administration"
    excluded_locations: str = "United States,US Remote,Bengaluru,Dublin,Tokyo"
    excluded_titles: str = "software engineer,legal counsel,product manager"

    @field_validator("ai_provider")
    @classmethod
    def validate_ai_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"ollama", "llama_cpp"}:
            raise ValueError("ai_provider must be 'ollama' or 'llama_cpp'")
        return normalized

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
