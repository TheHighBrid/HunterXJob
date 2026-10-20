"""Process-wide singletons shared by the API modules."""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.cycle import CycleRunner
from app.db import SessionLocal

settings = get_settings()
runner = CycleRunner(settings, SessionLocal, Path("run/cycle.lock"))


def get_runner() -> CycleRunner:
    """FastAPI dependency (overridable in tests)."""
    return runner
