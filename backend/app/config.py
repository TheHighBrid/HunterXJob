"""Application configuration, loaded from environment variables / .env file.

See .env.example for the full list of supported variables and their meaning.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- API auth ---
    API_KEY: str = "change-me-to-a-long-random-string"

    # --- Database ---
    DATABASE_PATH: str = "./data/hunterxjob.db"

    # --- LLM provider ---
    LLM_PROVIDER: str = "ollama"  # "ollama" | "openai_compatible"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
    OPENAI_COMPATIBLE_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_COMPATIBLE_API_KEY: str = ""
    OPENAI_COMPATIBLE_MODEL: str = "gpt-4o-mini"

    # --- Automation safety rails ---
    MAX_APPLICATIONS_PER_DAY: int = 15
    MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS: int = 90
    BLACKLISTED_COMPANIES: str = ""  # comma-separated
    AUTOMATION_DRY_RUN: bool = True
    AUTOMATION_ENABLED: bool = True

    # --- Job sources ---
    GREENHOUSE_BOARD_TOKENS: str = ""  # comma-separated
    LEVER_COMPANIES: str = ""  # comma-separated
    GENERIC_FEED_URLS: str = ""  # comma-separated

    # --- SMTP ---
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_FROM_ADDRESS: str = ""

    # --- IMAP ---
    IMAP_HOST: str = ""
    IMAP_PORT: int = 993
    IMAP_USERNAME: str = ""
    IMAP_PASSWORD: str = ""
    IMAP_MAILBOX: str = "INBOX"

    # --- Playwright ---
    PLAYWRIGHT_STORAGE_STATE_DIR: str = "./data/storage_states"
    RENDERED_DOCS_DIR: str = "./data/rendered"

    # --- Scheduler intervals (minutes) ---
    JOB_SEARCH_INTERVAL_MINUTES: int = 60
    AUTOMATION_INTERVAL_MINUTES: int = 15
    EMAIL_CHECK_INTERVAL_MINUTES: int = 30
    REPORT_INTERVAL_MINUTES: int = 1440

    LOG_LEVEL: str = "INFO"

    @property
    def blacklisted_companies_list(self) -> list[str]:
        return [c.strip().lower() for c in self.BLACKLISTED_COMPANIES.split(",") if c.strip()]

    @property
    def greenhouse_board_tokens_list(self) -> list[str]:
        return [t.strip() for t in self.GREENHOUSE_BOARD_TOKENS.split(",") if t.strip()]

    @property
    def lever_companies_list(self) -> list[str]:
        return [c.strip() for c in self.LEVER_COMPANIES.split(",") if c.strip()]

    @property
    def generic_feed_urls_list(self) -> list[str]:
        return [u.strip() for u in self.GENERIC_FEED_URLS.split(",") if u.strip()]

    def ensure_dirs(self) -> None:
        Path(self.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(self.PLAYWRIGHT_STORAGE_STATE_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.RENDERED_DOCS_DIR).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
