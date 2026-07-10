"""Effective settings = .env defaults layered with runtime overrides.

`PUT /api/settings` persists overrides into the `settings_kv` table. Every
place that needs one of these values at *run time* (safety rails, the
scheduler, adapter dry-run flag, LLM provider selection) must go through
`load_runtime_settings()` rather than reading `app.config.Settings`
directly - otherwise a change made through the API silently has no effect
until the process restarts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SettingsKV

_KV_KEYS = {
    "max_applications_per_day": "MAX_APPLICATIONS_PER_DAY",
    "min_delay_between_applications_seconds": "MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS",
    "blacklisted_companies": "BLACKLISTED_COMPANIES",
    "automation_dry_run": "AUTOMATION_DRY_RUN",
    "automation_enabled": "AUTOMATION_ENABLED",
    "llm_provider": "LLM_PROVIDER",
}


@dataclass
class RuntimeSettings:
    max_applications_per_day: int
    min_delay_between_applications_seconds: int
    blacklisted_companies: list[str]
    automation_dry_run: bool
    automation_enabled: bool
    llm_provider: str
    llm_model: str


def _get_kv(db: Session, key: str) -> str | None:
    row = db.get(SettingsKV, key)
    return row.value if row else None


def _set_kv(db: Session, key: str, value: str) -> None:
    row = db.get(SettingsKV, key)
    if row is None:
        row = SettingsKV(key=key, value=value)
    else:
        row.value = value
    db.add(row)


def _llm_model_for(provider: str, settings: Settings) -> str:
    return settings.OPENAI_COMPATIBLE_MODEL if provider == "openai_compatible" else settings.OLLAMA_MODEL


def load_runtime_settings(db: Session, settings: Settings) -> RuntimeSettings:
    max_apps = _get_kv(db, _KV_KEYS["max_applications_per_day"])
    min_delay = _get_kv(db, _KV_KEYS["min_delay_between_applications_seconds"])
    blacklist = _get_kv(db, _KV_KEYS["blacklisted_companies"])
    dry_run = _get_kv(db, _KV_KEYS["automation_dry_run"])
    enabled = _get_kv(db, _KV_KEYS["automation_enabled"])
    llm_provider = _get_kv(db, _KV_KEYS["llm_provider"])

    resolved_provider = llm_provider if llm_provider is not None else settings.LLM_PROVIDER

    return RuntimeSettings(
        max_applications_per_day=(
            int(max_apps) if max_apps is not None else settings.MAX_APPLICATIONS_PER_DAY
        ),
        min_delay_between_applications_seconds=(
            int(min_delay)
            if min_delay is not None
            else settings.MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS
        ),
        blacklisted_companies=(
            [c.strip().lower() for c in json.loads(blacklist) if c.strip()]
            if blacklist is not None
            else settings.blacklisted_companies_list
        ),
        automation_dry_run=(
            dry_run.lower() == "true" if dry_run is not None else settings.AUTOMATION_DRY_RUN
        ),
        automation_enabled=(
            enabled.lower() == "true" if enabled is not None else settings.AUTOMATION_ENABLED
        ),
        llm_provider=resolved_provider,
        llm_model=_llm_model_for(resolved_provider, settings),
    )


def save_runtime_settings_overrides(
    db: Session,
    *,
    max_applications_per_day: int | None = None,
    min_delay_between_applications_seconds: int | None = None,
    blacklisted_companies: list[str] | None = None,
    automation_dry_run: bool | None = None,
    automation_enabled: bool | None = None,
    llm_provider: str | None = None,
) -> None:
    if max_applications_per_day is not None:
        _set_kv(db, _KV_KEYS["max_applications_per_day"], str(max_applications_per_day))
    if min_delay_between_applications_seconds is not None:
        _set_kv(
            db,
            _KV_KEYS["min_delay_between_applications_seconds"],
            str(min_delay_between_applications_seconds),
        )
    if blacklisted_companies is not None:
        _set_kv(db, _KV_KEYS["blacklisted_companies"], json.dumps(blacklisted_companies))
    if automation_dry_run is not None:
        _set_kv(db, _KV_KEYS["automation_dry_run"], str(automation_dry_run))
    if automation_enabled is not None:
        _set_kv(db, _KV_KEYS["automation_enabled"], str(automation_enabled))
    if llm_provider is not None:
        _set_kv(db, _KV_KEYS["llm_provider"], llm_provider)
    db.commit()
