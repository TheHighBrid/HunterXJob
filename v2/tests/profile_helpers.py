"""Shared helpers: the made-up example profile (no real personal data)."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select

from app.material_workflow import approve_material
from app.models import ApplicationMaterial
from app.profile import import_profile_document

EXAMPLE_PROFILE = Path(__file__).resolve().parents[1] / "examples" / "profile.example.yaml"


def example_document(verified: bool = True) -> dict:
    document = yaml.safe_load(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    document["verified"] = verified
    return document


def load_example_profile(db, verified: bool = True):
    return import_profile_document(db, example_document(verified), origin="profile.example.yaml")


def approve_drafts(db, application_id: str) -> list[str]:
    rows = db.execute(select(ApplicationMaterial).where(
        ApplicationMaterial.application_id == application_id, ApplicationMaterial.status == "draft")).scalars().all()
    ids = [row.id for row in rows]
    for material_id in ids:
        approve_material(db, material_id)
    return ids


def memory_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.flags import ensure_flags

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ensure_flags(db)
    return db
