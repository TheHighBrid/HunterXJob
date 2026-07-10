"""GET/PUT /api/settings — automation config (caps, channels, LLM provider).

Runtime-mutable settings are persisted in the settings_kv table and layered
on top of the .env-derived defaults; .env values act as the initial seed and
as the fallback for anything not yet overridden at runtime.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.db import get_db
from app.models import SettingsKV
from app.schemas import SettingsOut, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_api_key)])

_KV_KEYS = {
    "max_applications_per_day": "MAX_APPLICATIONS_PER_DAY",
    "min_delay_between_applications_seconds": "MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS",
    "blacklisted_companies": "BLACKLISTED_COMPANIES",
    "automation_dry_run": "AUTOMATION_DRY_RUN",
    "llm_provider": "LLM_PROVIDER",
}


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


@router.get("", response_model=SettingsOut)
def get_current_settings(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> SettingsOut:
    max_apps = _get_kv(db, "max_applications_per_day")
    min_delay = _get_kv(db, "min_delay_between_applications_seconds")
    blacklist = _get_kv(db, "blacklisted_companies")
    dry_run = _get_kv(db, "automation_dry_run")
    llm_provider = _get_kv(db, "llm_provider")

    return SettingsOut(
        max_applications_per_day=int(max_apps) if max_apps is not None else settings.MAX_APPLICATIONS_PER_DAY,
        min_delay_between_applications_seconds=(
            int(min_delay) if min_delay is not None else settings.MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS
        ),
        blacklisted_companies=(
            json.loads(blacklist) if blacklist is not None else settings.blacklisted_companies_list
        ),
        automation_dry_run=(
            dry_run.lower() == "true" if dry_run is not None else settings.AUTOMATION_DRY_RUN
        ),
        llm_provider=llm_provider if llm_provider is not None else settings.LLM_PROVIDER,
    )


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SettingsOut:
    if payload.max_applications_per_day is not None:
        _set_kv(db, "max_applications_per_day", str(payload.max_applications_per_day))
    if payload.min_delay_between_applications_seconds is not None:
        _set_kv(
            db,
            "min_delay_between_applications_seconds",
            str(payload.min_delay_between_applications_seconds),
        )
    if payload.blacklisted_companies is not None:
        _set_kv(db, "blacklisted_companies", json.dumps(payload.blacklisted_companies))
    if payload.automation_dry_run is not None:
        _set_kv(db, "automation_dry_run", str(payload.automation_dry_run))
    if payload.llm_provider is not None:
        _set_kv(db, "llm_provider", payload.llm_provider)
    db.commit()

    return get_current_settings(db=db, settings=settings)
