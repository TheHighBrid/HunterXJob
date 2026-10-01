"""Phone-editable settings: a small, safe subset stored as runtime overrides.

The environment (``.env``) stays the source of truth for everything else. A
handful of operational knobs (caps, quiet hours, targeting) can be changed
from the mobile app; those overrides are stored in the ``setting_overrides``
table and applied over the environment values at startup and on update.

Safety rules enforced here:

* Only keys in :class:`SettingsPatch` can be changed. Unknown keys are refused
  (``extra="forbid"``), so ``allow_live_submission``, ``application_mode``,
  ``api_key`` and every other setting can never be changed through the API.
* Numeric limits keep the same bounds as :class:`app.config.Settings`.
* :func:`settings_view` never includes secrets (API key, URLs of local
  services, database path).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SettingOverride
from app.security import auth_posture

logger = logging.getLogger(__name__)

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
LIST_FIELDS = ("target_locations", "target_keywords", "excluded_locations", "excluded_titles", "blacklisted_companies")
MAX_LIST_ITEMS = 100
MAX_ITEM_LENGTH = 120

LIVE_SUBMISSION_LOCK_REASON = (
    "No adapter is certified for autonomous submission. The pipeline only runs dry-runs "
    "and has no submit path; this cannot be changed from the API."
)


class SettingsPatch(BaseModel):
    """The only settings the API may change. Anything else is a 422."""

    model_config = ConfigDict(extra="forbid")

    automation_enabled: bool | None = None
    max_applications_per_day: int | None = Field(default=None, ge=0, le=50)
    max_dry_runs_per_day: int | None = Field(default=None, ge=0, le=100)
    min_match_score: int | None = Field(default=None, ge=0, le=100)
    quiet_hours_start: str | None = Field(default=None, pattern=HHMM)
    quiet_hours_end: str | None = Field(default=None, pattern=HHMM)
    cycle_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    cycle_max_score: int | None = Field(default=None, ge=0, le=500)
    cycle_max_prepare: int | None = Field(default=None, ge=0, le=20)
    cycle_max_dry_runs: int | None = Field(default=None, ge=0, le=20)
    target_locations: list[str] | None = None
    target_keywords: list[str] | None = None
    excluded_locations: list[str] | None = None
    excluded_titles: list[str] | None = None
    blacklisted_companies: list[str] | None = None

    @field_validator(*LIST_FIELDS)
    @classmethod
    def _clean_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > MAX_LIST_ITEMS:
            raise ValueError(f"at most {MAX_LIST_ITEMS} items")
        cleaned: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
                continue
            if "," in item:
                raise ValueError("items cannot contain commas")
            if len(item) > MAX_ITEM_LENGTH:
                raise ValueError(f"items must be at most {MAX_ITEM_LENGTH} characters")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True, exclude_none=True)


EDITABLE_KEYS: tuple[str, ...] = tuple(SettingsPatch.model_fields)


def _to_setting_value(key: str, value: Any) -> Any:
    return ",".join(value) if key in LIST_FIELDS else value


def apply_changes(settings: Settings, changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        if key not in EDITABLE_KEYS:  # defensive: SettingsPatch already forbids this
            raise ValueError(f"{key} is not editable")
        setattr(settings, key, _to_setting_value(key, value))


def load_overrides(db: Session) -> dict[str, Any]:
    """Stored overrides that still validate (invalid or retired keys are skipped)."""
    raw: dict[str, Any] = {}
    for row in db.execute(select(SettingOverride)).scalars():
        try:
            raw[row.key] = json.loads(row.value_json)
        except ValueError:
            logger.warning("ignoring unreadable setting override %s", row.key)
    valid: dict[str, Any] = {}
    for key, value in raw.items():
        try:
            valid.update(SettingsPatch.model_validate({key: value}).changes())
        except ValidationError:
            logger.warning("ignoring invalid setting override %s", key)
    return valid


def apply_stored_overrides(db: Session, settings: Settings) -> dict[str, Any]:
    overrides = load_overrides(db)
    apply_changes(settings, overrides)
    return overrides


def save_patch(db: Session, settings: Settings, patch: SettingsPatch) -> dict[str, Any]:
    """Persist and apply a validated patch. Returns the applied changes."""
    changes = patch.changes()
    for key, value in changes.items():
        row = db.get(SettingOverride, key)
        if row is None:
            db.add(SettingOverride(key=key, value_json=json.dumps(value)))
        else:
            row.value_json = json.dumps(value)
    db.commit()
    apply_changes(settings, changes)
    return changes


def overridden_keys(db: Session) -> list[str]:
    return sorted(load_overrides(db))


def settings_view(settings: Settings, overridden: list[str] | None = None) -> dict[str, Any]:
    """Everything the phone may see about the configuration. Never secrets."""
    return {
        "auth_mode": auth_posture(settings).mode,
        "application_mode": settings.application_mode,
        "automation_enabled": settings.automation_enabled,
        "live_submission": {
            "locked": True,
            "env_allow_live_submission": settings.allow_live_submission,
            "reason": LIVE_SUBMISSION_LOCK_REASON,
        },
        "continuous_run_enabled": settings.continuous_run_enabled,
        "cycle_interval_minutes": settings.cycle_interval_minutes,
        "cycle_max_score": settings.cycle_max_score,
        "cycle_max_prepare": settings.cycle_max_prepare,
        "cycle_max_dry_runs": settings.cycle_max_dry_runs,
        "max_dry_runs_per_day": settings.max_dry_runs_per_day,
        "max_applications_per_day": settings.max_applications_per_day,
        "min_match_score": settings.min_match_score,
        "quiet_hours_start": settings.quiet_hours_start,
        "quiet_hours_end": settings.quiet_hours_end,
        "timezone": settings.timezone,
        "target_locations": settings.target_location_list,
        "target_keywords": settings.target_keyword_list,
        "excluded_locations": settings.excluded_location_list,
        "excluded_titles": settings.excluded_title_list,
        "blacklisted_companies": settings.blacklisted_company_list,
        "sources": settings.source_counts(),
        "llm_provider": settings.llm_provider,
        "llm_fast_model": settings.ollama_fast_model,
        "llm_quality_model": settings.ollama_quality_model,
        "greenhouse_browser_verify": settings.greenhouse_browser_verify,
        "backup_interval_hours": settings.backup_interval_hours,
        "backup_retention": settings.backup_retention,
        "editable": list(EDITABLE_KEYS),
        "overridden": overridden or [],
    }
