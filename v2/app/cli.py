"""Command-line entry points used by ./hunterx (python -m app.cli <command>)."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from app import __version__
from app.config import get_settings


def _cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _cmd_migrate(_: argparse.Namespace) -> int:
    from app.db import engine, migrate
    from app.migrations import LATEST_VERSION, current_version

    applied = migrate()
    print(f"schema v{current_version(engine)} (latest v{LATEST_VERSION}); applied: {applied or 'nothing'}")
    return 0


def _cmd_schema(_: argparse.Namespace) -> int:
    from app.db import engine
    from app.migrations import LATEST_VERSION, current_version

    version = current_version(engine)
    if version > LATEST_VERSION:
        print(f"schema v{version} is newer than this code (v{LATEST_VERSION}); upgrade or restore a backup")
        return 1
    pending = LATEST_VERSION - version
    print(f"schema v{version}" + (f"; {pending} migration(s) pending, applied automatically on start" if pending else ", up to date"))
    return 0


def _cmd_backup(args: argparse.Namespace) -> int:
    from app.backup import BackupError, create_backup

    settings = get_settings()
    keep = args.keep or settings.backup_retention
    try:
        result = create_backup(settings.database_file, Path(args.dir or settings.backup_dir), keep)
    except BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup written: {result.path} ({result.size_bytes} bytes); kept {keep}, pruned {len(result.pruned)}")
    return 0


def _cmd_backups(args: argparse.Namespace) -> int:
    from app.backup import list_backups

    settings = get_settings()
    for path in list_backups(Path(args.dir or settings.backup_dir)):
        print(f"{path}  {path.stat().st_size} bytes")
    return 0


def _cmd_cycle(_: argparse.Namespace) -> int:
    from app.cycle import CycleRunner
    from app.db import SessionLocal, init_db

    init_db()
    result = CycleRunner(get_settings(), SessionLocal, Path("run/cycle.lock")).run_once("cli")
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] in {"completed", "skipped"} else 1


def _cmd_autonomous_run(args: argparse.Namespace) -> int:
    import yaml
    from app.autonomous_loop import AutonomousConfig, AutonomousRunner
    from app.db import SessionLocal, init_db

    init_db()
    settings = get_settings()
    profile_data = None
    if getattr(args, "profile", None):
        profile_path = Path(args.profile)
        if profile_path.is_file():
            profile_data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    if getattr(args, "live", False):
        settings.allow_live_submission = True
        with SessionLocal() as db:
            from app.flags import set_flag
            set_flag(db, "allow_live_submission", True, "enabled via --live CLI flag")

    config = AutonomousConfig(
        allow_live_submission=bool(getattr(args, "live", False)),
        min_score_threshold=float(getattr(args, "min_score", 65.0) or 65.0),
        max_applications=int(getattr(args, "limit", 10) or 10),
        headless=not bool(getattr(args, "headed", False)),
        headed_pause_ms=int(getattr(args, "pause", 5) or 5) * 1000,
        max_errors=int(getattr(args, "max_errors", 3) or 3),
        profile_data=profile_data,
    )

    with SessionLocal() as db:
        runner = AutonomousRunner(db=db, settings=settings, config=config)
        summary = runner.run()
        print(json.dumps({
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "aborted_by_kill_switch": summary.aborted_by_kill_switch,
            "candidates_considered": summary.candidates_considered,
            "shortlisted": summary.shortlisted,
            "submitted_live": summary.submitted_live,
            "verified_dry_runs": summary.verified_dry_runs,
            "skipped_duplicates": summary.skipped_duplicates,
            "errors": summary.errors,
            "records": [
                {
                    "job_id": r.job_id,
                    "company": r.company,
                    "title": r.title,
                    "platform": r.platform,
                    "status": r.status,
                    "submitted": r.submitted,
                    "confirmation_receipt": r.confirmation_receipt,
                    "evidence_sha256": r.evidence_sha256,
                    "error": r.error,
                }
                for r in summary.records
            ],
        }, indent=2))
        return 0 if summary.errors == 0 and not summary.aborted_by_kill_switch else 1


def _cmd_dedup_scan(_: argparse.Namespace) -> int:
    from app.db import SessionLocal, init_db
    from app.dedup import scan

    init_db()
    with SessionLocal() as db:
        print(json.dumps(scan(db)))
    return 0


def _cmd_liveness(_: argparse.Namespace) -> int:
    from app.db import SessionLocal, init_db
    from app.job_liveness import run_due_checks

    init_db()
    with SessionLocal() as db:
        print(json.dumps(run_due_checks(db, get_settings()), default=str))
    return 0


def _cmd_api_key(_: argparse.Namespace) -> int:
    print(secrets.token_urlsafe(32))
    return 0


def _cmd_check_auth(_: argparse.Namespace) -> int:
    """Doctor helper: exit 0 = OK, 1 = FAIL, 2 = WARN."""
    from app.security import auth_posture

    posture = auth_posture(get_settings())
    print(posture.detail)
    return {"api_key": 0, "local_dev": 2}.get(posture.mode, 1)


OPENAPI_SNAPSHOT = Path(__file__).resolve().parent.parent / "openapi.json"


def openapi_text() -> str:
    """The API schema as stable, pretty JSON (what the mobile types are generated from)."""
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def _cmd_openapi(args: argparse.Namespace) -> int:
    text = openapi_text()
    target = Path(args.output) if args.output else OPENAPI_SNAPSHOT
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            print(f"{target} is out of date; run: ./hunterx openapi", file=sys.stderr)
            return 1
        print(f"{target} is up to date")
        return 0
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


COMMANDS = {
    "version": (_cmd_version, "print the version"),
    "migrate": (_cmd_migrate, "apply pending database migrations (backs up first)"),
    "schema": (_cmd_schema, "print the database schema version"),
    "backup": (_cmd_backup, "write a timestamped database backup and prune old ones"),
    "backups": (_cmd_backups, "list database backups"),
    "cycle": (_cmd_cycle, "run one discover/score/prepare/dry-run cycle now"),
    "dedup-scan": (_cmd_dedup_scan, "fingerprint older postings and link duplicates (never deletes)"),
    "liveness": (_cmd_liveness, "run due liveness checks for queued postings now (read-only GETs)"),
    "api-key": (_cmd_api_key, "print a new random API key"),
    "check-auth": (_cmd_check_auth, "report the API authentication posture"),
    "openapi": (_cmd_openapi, "write (or --check) the OpenAPI snapshot used for the mobile types"),
    "autonomous-run": (_cmd_autonomous_run, "run the fully autonomous discovery-to-submission engine"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunterx", description="HunterXJob v2 maintenance commands")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_handler, help_text) in COMMANDS.items():
        command = sub.add_parser(name, help=help_text)
        if name in {"backup", "backups"}:
            command.add_argument("--dir", help="backup directory (default: BACKUP_DIR)")
        if name == "openapi":
            command.add_argument("--output", help="file to write (default: v2/openapi.json)")
            command.add_argument("--check", action="store_true", help="exit 1 if the snapshot is out of date")
        if name == "backup":
            command.add_argument("--keep", type=int, help="number of backups to keep (default: BACKUP_RETENTION)")
        if name == "autonomous-run":
            command.add_argument("--live", action="store_true", help="enable live submission (requires ALLOW_LIVE_SUBMISSION=true)")
            command.add_argument("--limit", type=int, default=10, help="maximum applications to process in this run (default: 10)")
            command.add_argument("--min-score", type=float, default=65.0, help="minimum candidate match score threshold (default: 65.0)")
            command.add_argument("--profile", help="path to custom candidate profile YAML")
            command.add_argument("--headed", action="store_true", help="run browser in visible (headed) mode for inspection")
            command.add_argument("--pause", type=int, default=5, help="seconds to pause and inspect the filled form in headed mode (default: 5)")
            command.add_argument("--max-errors", type=int, default=3, help="maximum errors before stopping the cycle (default: 3)")
    from app.cli_profile import register

    register(sub)
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None) or COMMANDS[args.command][0]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
