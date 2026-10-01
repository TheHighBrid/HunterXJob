"""Continuous-run cycles: discover -> score -> prepare -> dry-run.

A cycle is bounded and conservative:

* It is skipped when the global kill switch is engaged, when the owner paused
  the scheduler (``scheduler_paused`` flag) or during quiet hours, and it stops
  early if the kill switch is engaged mid-cycle.
* "prepare" only drafts materials with the local model for shortlisted jobs.
  The owner still approves each application; the cycle never approves.
* "dry-run" only runs for applications the owner already approved
  (``ready_to_apply``), needs ``AUTOMATION_ENABLED=true``, and respects the
  daily submission cap and the daily dry-run cap. It calls the same
  ``execute_apply`` as the API, which never submits. If a result ever claimed
  a submission, the cycle engages the kill switch and stops.
* Cycles never overlap: a process-wide lock plus an ``flock`` on a lock file
  (so a CLI run and the API's scheduler cannot run at the same time).
* Every cycle, including skips, is recorded in ``scheduler_cycles``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import LocalAI
from app.backup import create_backup, latest_backup_time
from app.config import Settings
from app.discovery import JobRecord, discover_all, upsert_jobs
from app.flags import is_enabled, kill_switch_engaged, set_flag
from app.models import Application, Job, PipelineStage, SchedulerCycle, iso_utc
from app.pipeline import execute_apply, generate_materials, score_pending_jobs
from app.resume_facts import read_resume_facts
from app.scheduler import day_start_utc, dry_runs_today, in_quiet_hours, local_now, submissions_today

if TYPE_CHECKING:
    from app.real_forms import FormProvider

try:  # POSIX only (Linux, Android/Termux); elsewhere only the in-process lock applies.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

STEPS = ("discover", "score", "prepare", "dry_run")
TICK_SECONDS = 30.0


class LiveSubmissionLockViolation(RuntimeError):
    """A dry-run reported a submission. This must never happen."""


class CycleLock:
    """Non-blocking cross-thread and cross-process lock for cycles."""

    _process_lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        if not CycleLock._process_lock.acquire(blocking=False):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    CycleLock._process_lock.release()
                    return False
            self._fd = fd
            return True
        except BaseException:
            CycleLock._process_lock.release()
            raise

    def release(self) -> None:
        if self._fd is not None:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        CycleLock._process_lock.release()

    def busy(self) -> bool:
        if self._fd is not None:
            return True
        if not self.acquire():
            return True
        self.release()
        return False


@dataclass(slots=True)
class CycleDeps:
    """Collaborators a cycle uses; tests replace them to stay offline."""

    discover: Callable[[Settings, list[str]], list[JobRecord]] = discover_all
    resume_loader: Callable[[], str] = read_resume_facts
    ai_factory: Callable[[Settings], LocalAI] = LocalAI
    form_provider: FormProvider | None = None


@dataclass(slots=True)
class _Context:
    resume: str = ""
    ai_ok: bool = False
    aborted: bool = False
    notes: list[str] = field(default_factory=list)


def cycle_to_dict(cycle: SchedulerCycle) -> dict[str, Any]:
    return {
        "id": cycle.id,
        "trigger": cycle.trigger,
        "status": cycle.status,
        "reason": cycle.reason,
        "error": cycle.error,
        "started_at": iso_utc(cycle.started_at),
        "finished_at": iso_utc(cycle.finished_at),
        "steps": json.loads(cycle.steps_json) if cycle.steps_json else {},
    }


def recent_cycles(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.execute(select(SchedulerCycle).order_by(SchedulerCycle.started_at.desc()).limit(max(1, min(limit, 200)))).scalars()
    return [cycle_to_dict(row) for row in rows]


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason}


class CycleRunner:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        lock_path: Path = Path("run/cycle.lock"),
        deps: CycleDeps | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.lock = CycleLock(lock_path)
        self.deps = deps or CycleDeps()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_cycle_id: str | None = None
        self.next_run_at: datetime | None = None
        self.last_backup: dict[str, Any] | None = None

    # ----------------------------------------------------------------- running

    def run_once(self, trigger: str = "manual") -> dict[str, Any]:
        """Run one cycle now (or record why it could not run) and return its record."""
        if not self.lock.acquire():
            return self._record_overlap(trigger)
        try:
            with self.session_factory() as db:
                self._mark_interrupted(db)
                cycle = SchedulerCycle(trigger=trigger, status="running")
                db.add(cycle)
                db.commit()
                self._current_cycle_id = cycle.id
                # The ledger must record any failure, and the scheduler thread must survive it.
                try:
                    status, reason, steps = self._execute(db)
                    error = None
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.exception("cycle %s failed", cycle.id)
                    db.rollback()
                    status, reason, steps, error = "failed", "unexpected error", {}, f"{type(exc).__name__}: {exc}"
                cycle = db.get(SchedulerCycle, cycle.id)
                cycle.status = status
                cycle.reason = reason
                cycle.error = error
                cycle.steps_json = json.dumps(steps, default=str)
                cycle.finished_at = datetime.now(UTC)
                db.commit()
                return cycle_to_dict(cycle)
        finally:
            self._current_cycle_id = None
            self.lock.release()

    def _record_overlap(self, trigger: str) -> dict[str, Any]:
        with self.session_factory() as db:
            now = datetime.now(UTC)
            cycle = SchedulerCycle(
                trigger=trigger, status="skipped", reason="previous cycle still running", started_at=now, finished_at=now
            )
            db.add(cycle)
            db.commit()
            return cycle_to_dict(cycle)

    @staticmethod
    def _mark_interrupted(db: Session) -> None:
        # We hold the lock, so any cycle still marked running died with its process.
        for row in db.execute(select(SchedulerCycle).where(SchedulerCycle.status == "running")).scalars():
            row.status = "interrupted"
            row.finished_at = datetime.now(UTC)
            row.reason = "process stopped before the cycle finished"
        db.commit()

    def _gate(self, db: Session) -> str | None:
        if kill_switch_engaged(db):
            return "global kill switch engaged"
        if is_enabled(db, "scheduler_paused"):
            return "scheduler paused"
        if in_quiet_hours(local_now(self.settings), self.settings.quiet_hours_start, self.settings.quiet_hours_end):
            return "quiet hours"
        return None

    def _execute(self, db: Session) -> tuple[str, str | None, dict[str, Any]]:
        gate = self._gate(db)
        if gate:
            return "skipped", gate, {}
        ctx = _Context(resume=self.deps.resume_loader().strip())
        if ctx.resume:
            ctx.ai_ok = bool(self.deps.ai_factory(self.settings).health().get("ok"))
        handlers = {"discover": self._discover, "score": self._score, "prepare": self._prepare, "dry_run": self._dry_run}
        steps: dict[str, Any] = {}
        for name in STEPS:
            if ctx.aborted or kill_switch_engaged(db):
                ctx.aborted = True
                steps[name] = _skipped("global kill switch engaged")
                continue
            steps[name] = self._guarded(db, name, handlers[name], ctx)
        if ctx.aborted:
            return "aborted", "global kill switch engaged during the cycle", steps
        errors = [name for name, step in steps.items() if step.get("status") == "error" or step.get("errors")]
        return ("completed_with_errors" if errors else "completed"), None, steps

    @staticmethod
    def _guarded(db: Session, name: str, handler: Callable[[Session, _Context], dict[str, Any]], ctx: _Context) -> dict[str, Any]:
        # One failing step is recorded and must not stop the others.
        try:
            return handler(db, ctx)
        except LiveSubmissionLockViolation:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("cycle step %s failed", name)
            db.rollback()
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------- steps

    def _discover(self, db: Session, _ctx: _Context) -> dict[str, Any]:
        s = self.settings
        if not (s._csv(s.greenhouse_board_tokens) or s._csv(s.lever_companies)):
            return _skipped("no job sources configured (GREENHOUSE_BOARD_TOKENS / LEVER_COMPANIES)")
        errors: list[str] = []
        records = self.deps.discover(s, errors)
        added = upsert_jobs(db, records)
        return {"status": "ok", "discovered": len(records), "added": added, "errors": errors[:20]}

    def _score(self, db: Session, ctx: _Context) -> dict[str, Any]:
        if not ctx.resume:
            return _skipped("resume facts are not configured")
        if self.settings.cycle_max_score == 0:
            return _skipped("CYCLE_MAX_SCORE=0")
        processed = score_pending_jobs(db, self.settings, ctx.resume, use_ai=ctx.ai_ok, limit=self.settings.cycle_max_score)
        return {"status": "ok", "processed": processed, "ai_used": ctx.ai_ok}

    def _prepare(self, db: Session, ctx: _Context) -> dict[str, Any]:
        if not ctx.resume:
            return _skipped("resume facts are not configured")
        if not ctx.ai_ok:
            return _skipped("local AI is unavailable")
        if self.settings.cycle_max_prepare == 0:
            return _skipped("CYCLE_MAX_PREPARE=0")
        candidates = db.execute(
            select(Application).join(Job)
            .where(Job.stage == PipelineStage.shortlisted.value, Application.cover_letter_text.is_(None))
            .order_by(Job.final_score.desc().nullslast())
            .limit(self.settings.cycle_max_prepare)
        ).scalars().all()
        results = []
        for application in candidates:
            if kill_switch_engaged(db):
                ctx.aborted = True
                break
            updated = generate_materials(db, self.settings, application.id, ctx.resume)
            results.append({"application_id": updated.id, "stage": updated.stage, "error": updated.last_error})
        return {"status": "ok", "prepared": results, "note": "materials await owner approval"}

    def _dry_run_budget(self, db: Session) -> tuple[int, str | None]:
        s = self.settings
        if not s.automation_enabled:
            return 0, "automation disabled (AUTOMATION_ENABLED=false)"
        since = day_start_utc(s)
        if submissions_today(db, since) >= s.max_applications_per_day:
            return 0, "daily application cap reached"
        remaining = s.max_dry_runs_per_day - dry_runs_today(db, since)
        if remaining <= 0:
            return 0, "daily dry-run cap reached"
        budget = min(s.cycle_max_dry_runs, remaining)
        return budget, None if budget else "CYCLE_MAX_DRY_RUNS=0"

    def _dry_run(self, db: Session, ctx: _Context) -> dict[str, Any]:
        budget, reason = self._dry_run_budget(db)
        if reason:
            return _skipped(reason)
        candidates = db.execute(
            select(Application).join(Job)
            .where(Job.stage == PipelineStage.ready_to_apply.value)
            .order_by(Job.final_score.desc().nullslast())
            .limit(budget)
        ).scalars().all()
        results = []
        for application in candidates:
            if kill_switch_engaged(db):
                ctx.aborted = True
                break
            results.append(self._dry_run_one(db, application.id))
        return {"status": "ok", "attempted": len(results), "results": results, "submitted": 0}

    def _dry_run_one(self, db: Session, application_id: str) -> dict[str, Any]:
        try:
            result = execute_apply(db, self.settings, application_id, form_provider=self.deps.form_provider)
        except (RuntimeError, ValueError) as exc:
            return {"application_id": application_id, "status": "refused", "reason": str(exc)}
        if result.get("submitted") is not False:
            set_flag(db, "global_kill_switch", True, "engaged by scheduler: a dry-run reported a submission")
            raise LiveSubmissionLockViolation(f"dry-run for {application_id} did not report submitted=False")
        return {"application_id": application_id, "status": result.get("status"), "reason": result.get("reason")}

    # ------------------------------------------------------------- background

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hunterx-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def trigger_async(self) -> bool:
        """Start a manual cycle in the background. False if one is already running."""
        if self.lock.busy():
            return False
        threading.Thread(target=self.run_once, args=("manual",), name="hunterx-manual-cycle", daemon=True).start()
        return True

    def _loop(self) -> None:
        interval = timedelta(minutes=self.settings.cycle_interval_minutes)
        if self.settings.continuous_run_enabled:
            self.next_run_at = datetime.now(UTC) + timedelta(seconds=self.settings.cycle_startup_delay_seconds)
        while not self._stop.is_set():
            if self.next_run_at is not None and datetime.now(UTC) >= self.next_run_at:
                self.run_once("scheduled")
                self.next_run_at = datetime.now(UTC) + interval
            self.maybe_backup()
            self._stop.wait(TICK_SECONDS)

    def maybe_backup(self, now: datetime | None = None) -> dict[str, Any] | None:
        hours = self.settings.backup_interval_hours
        if hours <= 0:
            return None
        now = now or datetime.now(UTC)
        backup_dir = Path(self.settings.backup_dir)
        latest = latest_backup_time(backup_dir)
        if latest is not None and now - latest < timedelta(hours=hours):
            return None
        started = time.monotonic()
        try:
            result = create_backup(self.settings.database_file, backup_dir, self.settings.backup_retention)
            self.last_backup = {"ok": True, "path": str(result.path), "pruned": len(result.pruned),
                                "seconds": round(time.monotonic() - started, 2), "at": now.isoformat()}
        except (OSError, RuntimeError) as exc:
            logger.warning("scheduled backup failed: %s", exc)
            self.last_backup = {"ok": False, "error": str(exc), "at": now.isoformat()}
        return self.last_backup

    # ----------------------------------------------------------------- status

    def status(self, db: Session) -> dict[str, Any]:
        s = self.settings
        since = day_start_utc(s)
        running_row = db.execute(select(SchedulerCycle).where(SchedulerCycle.status == "running")).scalars().first()
        latest = latest_backup_time(Path(s.backup_dir))
        return {
            "continuous_run_enabled": s.continuous_run_enabled,
            "scheduler_thread_alive": self.alive,
            "paused": is_enabled(db, "scheduler_paused"),
            "kill_switch": kill_switch_engaged(db),
            "automation_enabled": s.automation_enabled,
            "live_submission": "locked: cycles only run dry-runs and never submit",
            "cycle_in_progress": self._current_cycle_id is not None or running_row is not None,
            "current_cycle_id": self._current_cycle_id or (running_row.id if running_row else None),
            "interval_minutes": s.cycle_interval_minutes,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "quiet_hours": {
                "start": s.quiet_hours_start,
                "end": s.quiet_hours_end,
                "timezone": s.timezone,
                "active": in_quiet_hours(local_now(s), s.quiet_hours_start, s.quiet_hours_end),
            },
            "limits": {
                "cycle_max_score": s.cycle_max_score,
                "cycle_max_prepare": s.cycle_max_prepare,
                "cycle_max_dry_runs": s.cycle_max_dry_runs,
                "max_dry_runs_per_day": s.max_dry_runs_per_day,
                "max_applications_per_day": s.max_applications_per_day,
            },
            "today": {"dry_runs": dry_runs_today(db, since), "submissions": submissions_today(db, since)},
            "backup": {
                "interval_hours": s.backup_interval_hours,
                "latest_at": latest.isoformat() if latest else None,
                "last_result": self.last_backup,
            },
            "last_cycle": next(iter(recent_cycles(db, 1)), None),
        }
