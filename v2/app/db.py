from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        # WAL lets the API read while a background cycle writes, and survives
        # an interrupted process without corrupting the database.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA busy_timeout=10000")
    finally:
        cursor.close()


def make_engine(url: str) -> Engine:
    created = create_engine(url, connect_args={"check_same_thread": False})
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        event.listen(created, "connect", _sqlite_pragmas)
    return created


settings = get_settings()
engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

_init_lock = threading.Lock()
_initialized = False


def _pre_migration_backup(target: Engine, database: Path) -> None:
    from app.backup import create_backup
    from app.migrations import has_user_tables

    if not database.is_file() or not has_user_tables(target):
        return
    current = get_settings()
    result = create_backup(database, Path(current.backup_dir), current.backup_retention)
    logger.info("pre-migration backup written to %s", result.path)


def migrate(target: Engine | None = None, database: Path | None = None) -> list[int]:
    """Apply pending schema migrations, backing up an existing database first."""
    from app import models  # noqa: F401  (registers tables)
    from app.migrations import run_migrations

    target = target or engine
    database = database or settings.database_file
    return run_migrations(target, before=lambda _pending: _pre_migration_backup(target, database))


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if not _initialized:
            migrate()
            _initialized = True


def get_db() -> Generator[Session, None, None]:
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
