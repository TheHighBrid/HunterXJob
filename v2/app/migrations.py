"""Versioned SQLite schema migrations.

Each migration has an integer version and is applied once, in order, inside a
transaction; the ``schema_version`` table records what has been applied. A
database created before versioning existed is adopted by the baseline
migration (its tables already exist, so creating them is a no-op).

To change the schema: add a model change *and* a new ``Migration`` with the
next version number. Never edit or renumber a released migration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, inspect, select

from app.db import Base

BASELINE_TABLES = (
    "jobs",
    "applications",
    "pipeline_events",
    "answer_policies",
    "review_tasks",
    "submission_evidence",
    "feature_flags",
)


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer HunterXJob than the running code."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    description: str
    apply: Callable[[Connection], None]


def _create_tables(*names: str) -> Callable[[Connection], None]:
    def apply(connection: Connection) -> None:
        tables = [Base.metadata.tables[name] for name in names]
        Base.metadata.create_all(connection, tables=tables, checkfirst=True)

    return apply


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline schema (v0.2)", _create_tables(*BASELINE_TABLES)),
    Migration(2, "scheduler cycle ledger", _create_tables("scheduler_cycles")),
    Migration(3, "runtime setting overrides", _create_tables("setting_overrides")),
)
LATEST_VERSION = MIGRATIONS[-1].version


def _ensure_version_table(connection: Connection) -> None:
    from app import models  # noqa: F401  (registers the tables on Base.metadata)

    Base.metadata.create_all(connection, tables=[Base.metadata.tables["schema_version"]], checkfirst=True)


def current_version(engine: Engine) -> int:
    """Return the applied schema version (0 for a new or pre-versioning database)."""
    from app.models import SchemaVersion

    if not inspect(engine).has_table("schema_version"):
        return 0
    with engine.connect() as connection:
        versions = connection.execute(select(SchemaVersion.version)).scalars().all()
    return max(versions, default=0)


def pending_migrations(engine: Engine) -> list[Migration]:
    version = current_version(engine)
    if version > LATEST_VERSION:
        raise SchemaTooNewError(
            f"database schema is v{version} but this code only knows up to v{LATEST_VERSION}; "
            "upgrade HunterXJob or restore a backup taken by this version"
        )
    return [migration for migration in MIGRATIONS if migration.version > version]


def has_user_tables(engine: Engine) -> bool:
    return any(inspect(engine).has_table(name) for name in BASELINE_TABLES)


def run_migrations(engine: Engine, before: Callable[[list[Migration]], None] | None = None) -> list[int]:
    """Apply pending migrations in order. Returns the versions applied.

    ``before`` is called with the pending list (only when there is something to
    apply), e.g. to take a pre-migration backup.
    """
    from app.models import SchemaVersion

    pending = pending_migrations(engine)
    if not pending:
        return []
    if before is not None:
        before(pending)
    applied: list[int] = []
    for migration in pending:
        with engine.begin() as connection:
            _ensure_version_table(connection)
            migration.apply(connection)
            connection.execute(
                SchemaVersion.__table__.insert().values(version=migration.version, description=migration.description)
            )
        applied.append(migration.version)
    return applied
