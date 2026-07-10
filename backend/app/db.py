"""SQLAlchemy engine/session setup for the (sync) SQLite database."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    settings.ensure_dirs()
    db_path = settings.DATABASE_PATH
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def reset_engine() -> None:
    """Rebuild the engine/session factory from current settings.

    Used by tests (after monkeypatching env vars / clearing the settings
    cache) to point the app at a fresh temporary database.
    """
    global engine, SessionLocal
    engine = _make_engine()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Create all tables. Safe to call repeatedly."""
    import app.models  # noqa: F401  ensure models are registered on Base.metadata

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
