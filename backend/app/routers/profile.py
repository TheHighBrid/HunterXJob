"""GET/PUT /api/profile — the single user's identity/contact/work-auth info.

Not explicitly listed in docs/ARCHITECTURE.md section 5's API surface list,
but the `profile` table (section 4) needs a way to be populated, and
automation_cycle/content_generation depend on it existing. Added as a small,
consistent extension of the documented API rather than a hidden side
channel.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.db import get_db
from app.models import Profile
from app.schemas import ProfileCreate, ProfileOut

router = APIRouter(prefix="/api/profile", tags=["profile"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db)) -> Profile:
    profile = db.execute(select(Profile)).scalars().first()
    if profile is None:
        profile = Profile()
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.put("", response_model=ProfileOut)
def update_profile(payload: ProfileCreate, db: Session = Depends(get_db)) -> Profile:
    profile = db.execute(select(Profile)).scalars().first()
    if profile is None:
        profile = Profile()

    for field, value in payload.model_dump().items():
        setattr(profile, field, value)

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile
