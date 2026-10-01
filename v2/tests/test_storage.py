import os
import sqlite3
import stat
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app import __version__
from app.backup import BackupError, create_backup, list_backups, prune_backups
from app.db import Base, make_engine
from app.migrations import (
    BASELINE_TABLES,
    LATEST_VERSION,
    SchemaTooNewError,
    current_version,
    pending_migrations,
    run_migrations,
)
from app.models import FeatureFlag, Job, SchemaVersion


def _engine(tmp_path: Path, name: str = "hx.db"):
    return make_engine(f"sqlite:///{tmp_path / name}")


# --- migrations ---------------------------------------------------------------

def test_fresh_database_migrates_to_latest_and_has_every_model_table(tmp_path):
    engine = _engine(tmp_path)
    assert current_version(engine) == 0
    applied = run_migrations(engine)
    assert applied == list(range(1, LATEST_VERSION + 1))
    assert current_version(engine) == LATEST_VERSION
    tables = set(inspect(engine).get_table_names())
    # A model added without a migration would be missing here.
    assert set(Base.metadata.tables) <= tables
    assert run_migrations(engine) == []


def test_pre_versioning_database_is_adopted_without_losing_data(tmp_path):
    engine = _engine(tmp_path)
    legacy = MetaData()
    for name in BASELINE_TABLES:
        Base.metadata.tables[name].to_metadata(legacy)
    legacy.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Job(source="greenhouse", external_id="1", title="Fraud Analyst", company="acme", url="https://x"))
        db.commit()
    assert current_version(engine) == 0
    assert not inspect(engine).has_table("scheduler_cycles")

    seen = []
    assert run_migrations(engine, before=lambda pending: seen.append([m.version for m in pending])) == [1, 2, 3, 4]
    assert seen == [[1, 2, 3, 4]]
    assert inspect(engine).has_table("scheduler_cycles")
    assert inspect(engine).has_table("setting_overrides")
    assert inspect(engine).has_table("profile_facts")
    assert inspect(engine).has_table("application_materials")
    with Session() as db:
        assert db.execute(select(Job.title)).scalar_one() == "Fraud Analyst"
        assert [row.version for row in db.execute(select(SchemaVersion)).scalars()] == [1, 2, 3, 4]


def test_before_hook_not_called_when_up_to_date(tmp_path):
    engine = _engine(tmp_path)
    run_migrations(engine)
    calls = []
    run_migrations(engine, before=calls.append)
    assert calls == []


def test_newer_schema_is_refused(tmp_path):
    engine = _engine(tmp_path)
    run_migrations(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO schema_version (version, description, applied_at) VALUES (999, 'future', '2030-01-01')"))
    with pytest.raises(SchemaTooNewError):
        pending_migrations(engine)


def test_file_databases_use_wal(tmp_path):
    engine = _engine(tmp_path)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"


# --- backups ------------------------------------------------------------------

def _seeded_db(tmp_path: Path) -> Path:
    engine = _engine(tmp_path)
    run_migrations(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(FeatureFlag(key="global_kill_switch", enabled=True, note="seed"))
        db.commit()
    engine.dispose()
    return tmp_path / "hx.db"


def test_backup_is_consistent_private_and_restorable(tmp_path):
    database = _seeded_db(tmp_path)
    result = create_backup(database, tmp_path / "backups", retention=3)
    assert result.path.exists() and result.path.name.startswith("hunterxjob-v2-")
    assert stat.S_IMODE(os.stat(result.path).st_mode) == 0o600
    with sqlite3.connect(result.path) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT enabled FROM feature_flags WHERE key='global_kill_switch'").fetchone()[0] == 1
        assert copy.execute("SELECT max(version) FROM schema_version").fetchone()[0] == LATEST_VERSION
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_backup_retention_keeps_newest(tmp_path):
    database = _seeded_db(tmp_path)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    paths = [create_backup(database, tmp_path / "b", retention=2, now=start + timedelta(hours=i)).path for i in range(4)]
    assert list_backups(tmp_path / "b") == paths[-2:]
    (tmp_path / "b" / "unrelated.db").write_text("keep me")
    assert prune_backups(tmp_path / "b", 1) == [paths[-2]]
    assert (tmp_path / "b" / "unrelated.db").exists()


def test_backup_errors(tmp_path):
    with pytest.raises(BackupError):
        create_backup(tmp_path / "missing.db", tmp_path / "b", retention=2)
    with pytest.raises(BackupError):
        prune_backups(tmp_path, 0)


def test_cli_backup_command(tmp_path, monkeypatch, capsys):
    from app import cli
    from app.config import Settings

    database = _seeded_db(tmp_path)
    settings = Settings(_env_file=None, database_path=str(database), backup_dir=str(tmp_path / "cli"), backup_retention=5)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["backup", "--keep", "1"]) == 0
    assert cli.main(["backup", "--keep", "1"]) == 0
    assert len(list_backups(tmp_path / "cli")) == 1
    assert "backup written" in capsys.readouterr().out


# --- version ------------------------------------------------------------------

def test_version_has_a_single_source():
    from app.main import app

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in pyproject["project"]
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "app.__version__"}
    assert app.version == __version__


def test_installed_metadata_matches_the_package_version():
    from importlib.metadata import version

    assert version("hunterxjob-v2") == __version__
