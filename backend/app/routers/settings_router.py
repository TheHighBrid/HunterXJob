"""GET/PUT /api/settings — automation config (caps, channels, LLM provider).

Runtime-mutable settings are persisted in the settings_kv table and layered
on top of the .env-derived defaults; .env values act as the initial seed and
as the fallback for anything not yet overridden at runtime. The same
layering (app.services.runtime_settings) is used by the scheduler/safety
checks, so a change made here actually takes effect on the next cycle.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import SettingsOut, SettingsUpdate
from app.services.runtime_settings import load_runtime_settings, save_runtime_settings_overrides

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=SettingsOut)
def get_current_settings(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> SettingsOut:
    runtime = load_runtime_settings(db, settings)
    return SettingsOut(**runtime.__dict__)


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SettingsOut:
    save_runtime_settings_overrides(db, **payload.model_dump(exclude_unset=True))
    return get_current_settings(db=db, settings=settings)
