from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    host: str = "127.0.0.1"
    port: int = 8011
    database_path: str = "./data/hunterxjob-v2.db"
    application_mode: str = Field(default="dry_run", pattern="^(review|dry_run|autonomous)$")
    automation_enabled: bool = False
    max_applications_per_day: int = 5
    min_match_score: int = 60

    llm_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_fast_model: str = "llama3.2:1b"
    ollama_quality_model: str = "llama3.2:3b"
    ai_connect_timeout: float = 10.0
    ai_generation_timeout: float = 1200.0
    ai_max_retries: int = 2
    ai_keep_alive: str = "60m"

    target_locations: str = "Ottawa,Gatineau,National Capital Region,Remote Canada,Canada"
    target_keywords: str = "fraud,disputes,AML,KYC,compliance,collections,credit,bilingual"
    excluded_locations: str = "United States,US Remote,Bengaluru,Dublin,Tokyo"
    excluded_titles: str = "software engineer,legal counsel,product manager"
    blacklisted_companies: str = ""

    greenhouse_board_tokens: str = ""
    lever_companies: str = ""
    generic_feed_urls: str = ""

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def target_location_list(self) -> list[str]:
        return self._csv(self.target_locations)

    @property
    def target_keyword_list(self) -> list[str]:
        return self._csv(self.target_keywords)

    @property
    def excluded_location_list(self) -> list[str]:
        return self._csv(self.excluded_locations)

    @property
    def excluded_title_list(self) -> list[str]:
        return self._csv(self.excluded_titles)

    @property
    def blacklisted_company_list(self) -> list[str]:
        return self._csv(self.blacklisted_companies)

    @property
    def database_url(self) -> str:
        path = Path(self.database_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
