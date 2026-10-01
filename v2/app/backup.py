"""Consistent, timestamped SQLite backups with retention.

Uses SQLite's online backup API, so a copy taken while the API or a cycle is
writing is still a consistent snapshot (WAL contents included). Each copy is
written to a temporary name, integrity-checked, then renamed into place, and
is readable only by the owner because it contains personal data.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BACKUP_PREFIX = "hunterxjob-v2-"
BACKUP_SUFFIX = ".db"


class BackupError(RuntimeError):
    pass


@dataclass(slots=True)
class BackupResult:
    path: Path
    size_bytes: int
    pruned: list[Path]


def list_backups(backup_dir: Path) -> list[Path]:
    """Backups in ``backup_dir``, oldest first (names sort chronologically)."""
    if not backup_dir.is_dir():
        return []
    return sorted(
        path for path in backup_dir.iterdir()
        if path.is_file() and path.name.startswith(BACKUP_PREFIX) and path.name.endswith(BACKUP_SUFFIX)
    )


def latest_backup_time(backup_dir: Path) -> datetime | None:
    backups = list_backups(backup_dir)
    if not backups:
        return None
    return datetime.fromtimestamp(backups[-1].stat().st_mtime, tz=UTC)


def prune_backups(backup_dir: Path, retention: int) -> list[Path]:
    """Delete the oldest backups so that at most ``retention`` remain."""
    if retention < 1:
        raise BackupError("retention must be at least 1")
    backups = list_backups(backup_dir)
    doomed = backups[: max(0, len(backups) - retention)]
    for path in doomed:
        path.unlink()
    return doomed


def create_backup(database: Path, backup_dir: Path, retention: int, now: datetime | None = None) -> BackupResult:
    """Copy ``database`` to ``backup_dir`` and prune old copies."""
    database = database.expanduser().resolve()
    if not database.is_file():
        raise BackupError(f"database not found: {database}")
    backup_dir = backup_dir.expanduser().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    partial = target.with_name(target.name + ".partial")

    source = sqlite3.connect(database)
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        destination = sqlite3.connect(partial)
        try:
            source.backup(destination)
            status = destination.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            destination.close()
    finally:
        source.close()
    if status != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError(f"backup failed integrity check: {status}")
    partial.replace(target)
    os.chmod(target, 0o600)
    pruned = prune_backups(backup_dir, retention)
    return BackupResult(path=target, size_bytes=target.stat().st_size, pruned=pruned)
