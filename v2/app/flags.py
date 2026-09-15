from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeatureFlag

DEFAULT_FLAGS = {
    "global_kill_switch": False,
    "unattended_mode": False,
    "allow_live_submission": False,
    "adapter.greenhouse": True,
    "adapter.lever": True,
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
