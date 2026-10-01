from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeatureFlag

DEFAULT_FLAGS = {
    "global_kill_switch": False,
    "unattended_mode": False,
    # Pauses scheduled continuous-run cycles without a restart (the kill switch
    # stops everything; this only stops new cycles).
    "scheduler_paused": False,
    "allow_live_submission": False,
    "adapter.greenhouse": True,
    "adapter.lever": True,
    "adapter.ashby": True,
    "adapter.email": True,
    "adapter.generic": True,
    "adapter.smartrecruiters": False,
    "adapter.workday": False,
    "adapter.icims": False,
    "adapter.taleo": False,
    "adapter.government": False,
}


def ensure_flags(db: Session) -> None:
    existing = {row.key for row in db.execute(select(FeatureFlag)).scalars()}
    dirty = False
    for key, enabled in DEFAULT_FLAGS.items():
        if key not in existing:
            db.add(FeatureFlag(key=key, enabled=enabled, note="default"))
            dirty = True
    if dirty:
        db.commit()


def is_enabled(db: Session, key: str) -> bool:
    ensure_flags(db)
    flag = db.get(FeatureFlag, key)
    return bool(flag and flag.enabled)


def set_flag(db: Session, key: str, enabled: bool, note: str = "") -> FeatureFlag:
    flag = db.get(FeatureFlag, key)
    if flag is None:
        flag = FeatureFlag(key=key, enabled=enabled, note=note)
        db.add(flag)
    else:
        flag.enabled = enabled
        if note:
            flag.note = note
    db.commit()
    db.refresh(flag)
    return flag


def snapshot(db: Session) -> dict[str, bool]:
    ensure_flags(db)
    return {row.key: row.enabled for row in db.execute(select(FeatureFlag)).scalars()}


def kill_switch_engaged(db: Session) -> bool:
    return is_enabled(db, "global_kill_switch")


# ----------------------------------------------------------------- API policy

KILL_SWITCH = "global_kill_switch"
# Flags on the path to a live submission. The API may turn them off, never on;
# enabling them needs local access to the database (and a certified adapter,
# which does not exist yet).
API_ENABLE_FORBIDDEN = frozenset({"allow_live_submission", "unattended_mode"})


class FlagChangeRefused(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def check_api_flag_change(key: str, enabled: bool, confirm: bool = False) -> None:
    """Raise FlagChangeRefused unless the API may set ``key`` to ``enabled``.

    * unknown keys are refused (404);
    * live-submission flags can only be turned off (403);
    * disengaging the kill switch needs an explicit ``confirm`` (428);
      engaging it is always allowed.
    """
    if key not in DEFAULT_FLAGS:
        raise FlagChangeRefused(404, f"unknown flag {key}")
    if enabled and key in API_ENABLE_FORBIDDEN:
        raise FlagChangeRefused(403, f"{key} cannot be enabled through the API; live submission stays locked")
    if key == KILL_SWITCH and not enabled and not confirm:
        raise FlagChangeRefused(428, "disengaging the kill switch needs confirm=true")
