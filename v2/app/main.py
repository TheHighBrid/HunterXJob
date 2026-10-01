from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.adapter_runtime import ADAPTER_CATALOG
from app.ai import LocalAI
from app.config import Settings, get_settings
from app.cycle import CycleRunner, recent_cycles
from app.db import SessionLocal, get_db, init_db
from app.discovery import discover_all, upsert_jobs
from app.flags import ensure_flags, set_flag, snapshot
from app.greenhouse_form import FormFetchError
from app.models import Application, Job, PipelineEvent, ReviewTask
from app.pipeline import approve_application, execute_apply, generate_materials, preview_form, score_pending_jobs
from app.real_forms import LiveFormProvider
from app.resume_facts import RESUME_FACTS_PATH, read_resume_facts
from app.review_queue import list_open, resolve_task
from app.scheduler import can_run_unattended, day_start_utc, submissions_today
from app.security import auth_posture, is_authorized, require_api_key
from app.vault_store import upsert_answer

settings = get_settings()
runner = CycleRunner(settings, SessionLocal, Path("run/cycle.lock"))

DbSession = Annotated[Session, Depends(get_db)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    db = SessionLocal()
    try:
        ensure_flags(db)
    finally:
        db.close()
    # One background thread runs scheduled cycles (if CONTINUOUS_RUN_ENABLED)
    # and periodic backups (if BACKUP_INTERVAL_HOURS > 0).
    background = settings.continuous_run_enabled or settings.backup_interval_hours > 0
    if background:
        runner.start()
    try:
        yield
    finally:
        if background:
            runner.stop()


# Interactive API docs expose the full schema; only serve them in local dev mode.
_docs = settings.local_dev_mode
app = FastAPI(
    title="HunterXJob v2",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _docs else None,
)
api = APIRouter(dependencies=[Depends(require_api_key)])


class ResumeFactsIn(BaseModel):
    text: str


class AnswerIn(BaseModel):
    key: str
    value: str
    source: str = "user"
    scope: str = "global"
    confidence: float = 1.0
    sensitive: bool = False
    note: str = ""


class FlagIn(BaseModel):
    enabled: bool
    note: str = ""


class ReviewResolveIn(BaseModel):
    resolution: str = Field(default="resolved")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>HunterXJob v2</title>
<style>body{font-family:system-ui;background:#111;color:#eee;max-width:960px;margin:auto;padding:24px}button{padding:12px;margin:4px;background:#eee;color:#111;border:0;border-radius:8px}input{padding:10px;width:60%;border-radius:8px;border:0}pre{white-space:pre-wrap;background:#1d1d1d;padding:16px;border-radius:10px}</style></head>
<body><h1>HunterXJob v2</h1><p>Bounded autonomy. Dry-run by default. Live submit stays locked until an adapter is certified.</p>
<p><input id='key' type='password' placeholder='API key (stored in this browser only)'><button onclick=\"saveKey()\">Save key</button></p>
<button onclick=\"run('/api/discovery/run')\">Discover</button>
<button onclick=\"run('/api/scoring/run')\">Score</button>
<button onclick=\"run('/api/scheduler/run')\">Run cycle</button>
<button onclick=\"load('/api/scheduler/status')\">Scheduler</button>
<button onclick=\"load('/api/jobs')\">Jobs</button>
<button onclick=\"load('/api/applications')\">Applications</button>
<button onclick=\"load('/api/review-tasks')\">Review queue</button>
<button onclick=\"load('/api/adapters')\">Adapters</button>
<button onclick=\"load('/api/flags')\">Flags</button>
<button onclick=\"load('/api/health')\">Health</button>
<pre id='out'>Ready.</pre>
<script>
key.value=localStorage.getItem('hunterxKey')||'';
function saveKey(){localStorage.setItem('hunterxKey',key.value);out.textContent='Key saved in this browser.'}
function hdrs(){return {'X-API-Key':localStorage.getItem('hunterxKey')||''}}
async function run(u){out.textContent='Working...';let r=await fetch(u,{method:'POST',headers:hdrs()});out.textContent=JSON.stringify(await r.json(),null,2)}
async function load(u){let r=await fetch(u,{headers:hdrs()});out.textContent=JSON.stringify(await r.json(),null,2)}
</script></body></html>"""


@app.get("/api/health")
def health(request: Request, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Liveness probe. Open to everyone; details only for authenticated callers."""
    posture = auth_posture(current)
    public: dict[str, object] = {"ok": True, "version": __version__, "auth": posture.mode}
    if not is_authorized(request, current):
        return public
    decision = can_run_unattended(db, settings)
    return {
        **public,
        "mode": settings.application_mode,
        "automation_enabled": settings.automation_enabled,
        "allow_live_submission": settings.allow_live_submission,
        "continuous_run_enabled": settings.continuous_run_enabled,
        "unattended_allowed": decision.allowed,
        "unattended_reason": decision.reason,
        "submissions_today": submissions_today(db, day_start_utc(settings)),
        "open_review_tasks": db.execute(select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")).scalar_one(),
        "flags": snapshot(db),
        "ai": LocalAI(settings).health(),
        "resume_loaded": RESUME_FACTS_PATH.exists(),
    }


@api.put("/api/resume-facts")
def save_resume_facts(payload: ResumeFactsIn) -> dict[str, object]:
    RESUME_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESUME_FACTS_PATH.write_text(payload.text.strip(), encoding="utf-8")
    return {"saved": True, "characters": len(payload.text.strip())}


@api.post("/api/answers")
def save_answer(payload: AnswerIn, db: DbSession) -> dict[str, object]:
    row = upsert_answer(db, **payload.model_dump())
    return {"id": row.id, "key": row.key, "scope": row.scope, "source": row.source}


@api.post("/api/discovery/run")
def run_discovery(db: DbSession) -> dict[str, object]:
    errors: list[str] = []
    records = discover_all(settings, errors)
    added = upsert_jobs(db, records)
    return {"discovered": len(records), "added": added, "errors": errors}


@api.post("/api/scoring/run")
def run_scoring(db: DbSession) -> dict[str, int]:
    resume = read_resume_facts()
    if not resume:
        raise HTTPException(409, "resume facts are not configured")
    return {"processed": score_pending_jobs(db, settings, resume, use_ai=True)}


@api.get("/api/jobs")
def list_jobs(db: DbSession, limit: int = 100) -> list[dict[str, object]]:
    rows = db.execute(select(Job).order_by(Job.final_score.desc().nullslast()).limit(min(limit, 500))).scalars()
    return [{
        "id": j.id, "title": j.title, "company": j.company, "location": j.location,
        "stage": j.stage, "eligible": j.eligible, "score": j.final_score, "url": j.url,
        "reason": j.eligibility_reason, "platform": j.platform,
    } for j in rows]


@api.get("/api/jobs/{job_id}/form")
def job_form(job_id: str, db: DbSession) -> dict[str, object]:
    """Fetch the job's real application form (read-only) and plan it against the vault.

    No state changes, no evidence, nothing submitted. Values for sensitive or
    legal fields are redacted in the response.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    try:
        form = LiveFormProvider(settings).fetch(job)
    except FormFetchError as exc:
        raise HTTPException(502 if exc.reason_code == "form_fetch_failed" else 409, {"reason": exc.reason_code, "detail": exc.detail}) from exc
    plan, summary = preview_form(db, form)
    return {
        "form": summary,
        "ready": plan.ready,
        "blockers": plan.blockers,
        "fields": [{
            "key": item.control.key,
            "label": item.control.label,
            "type": item.control.control_type.value,
            "section": item.control.section,
            "required": item.control.required,
            "sensitive": item.control.sensitive,
            "legal": item.control.legal,
            "options": len(item.control.options),
            "status": item.status,
            "reason": item.reason,
            "value": ("[set]" if item.control.sensitive or item.control.legal else item.value) if item.status == "fill" else None,
        } for item in plan.items],
    }


@api.get("/api/applications")
def list_applications(db: DbSession) -> list[dict[str, object]]:
    rows = db.execute(select(Application).order_by(Application.created_at.desc())).scalars()
    return [{
        "id": a.id, "job_id": a.job_id, "stage": a.stage, "mode": a.mode,
        "cover_letter_text": a.cover_letter_text, "last_error": a.last_error,
        "attempts": a.attempts, "adapter": a.adapter_name, "maturity": a.adapter_maturity,
        "idempotency_key": a.idempotency_key,
    } for a in rows]


@api.post("/api/applications/{application_id}/generate")
def generate(application_id: str, db: DbSession) -> dict[str, object]:
    resume = read_resume_facts()
    if not resume:
        raise HTTPException(409, "resume facts are not configured")
    try:
        result = generate_materials(db, settings, application_id, resume)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": result.id, "stage": result.stage, "last_error": result.last_error}


@api.post("/api/applications/{application_id}/approve")
def approve(application_id: str, db: DbSession) -> dict[str, object]:
    try:
        result = approve_application(db, application_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": result.id, "stage": result.stage, "job_stage": result.job.stage}


@api.post("/api/applications/{application_id}/apply")
def apply(application_id: str, db: DbSession) -> dict[str, object]:
    try:
        return execute_apply(db, settings, application_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@api.get("/api/review-tasks")
def review_tasks(db: DbSession) -> list[dict[str, object]]:
    return [{
        "id": t.id, "reason_code": t.reason_code, "status": t.status,
        "title": t.title, "detail": t.detail, "url": t.url,
        "application_id": t.application_id, "job_id": t.job_id,
    } for t in list_open(db)]


@api.post("/api/review-tasks/{task_id}/resolve")
def resolve_review(task_id: str, payload: ReviewResolveIn, db: DbSession) -> dict[str, object]:
    try:
        task = resolve_task(db, task_id, payload.resolution)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": task.id, "status": task.status}


@api.get("/api/adapters")
def adapters() -> list[dict[str, object]]:
    return [{
        "name": info.name,
        "maturity": info.maturity.value,
        "feature_flag": info.feature_flag,
        "notes": info.notes,
        "version": info.version,
    } for info in ADAPTER_CATALOG.values()]


@api.get("/api/flags")
def flags(db: DbSession) -> dict[str, bool]:
    return snapshot(db)


@api.put("/api/flags/{key}")
def update_flag(key: str, payload: FlagIn, db: DbSession) -> dict[str, object]:
    flag = set_flag(db, key, payload.enabled, payload.note)
    return {"key": flag.key, "enabled": flag.enabled, "note": flag.note}


@api.get("/api/events/{job_id}")
def events(job_id: str, db: DbSession) -> list[dict[str, object]]:
    rows = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job_id).order_by(PipelineEvent.created_at)).scalars()
    return [{"from": e.from_stage, "to": e.to_stage, "message": e.message, "created_at": e.created_at} for e in rows]


@api.get("/api/scheduler/status")
def scheduler_status(db: DbSession) -> dict[str, object]:
    return runner.status(db)


@api.get("/api/scheduler/cycles")
def scheduler_cycles(db: DbSession, limit: int = 20) -> list[dict[str, object]]:
    return recent_cycles(db, limit)


@api.post("/api/scheduler/run", status_code=202)
def scheduler_run() -> JSONResponse:
    """Start one cycle now in the background (same gates and caps as scheduled cycles)."""
    if not runner.trigger_async():
        return JSONResponse({"started": False, "reason": "a cycle is already running"}, status_code=409)
    return JSONResponse({"started": True, "status_url": "/api/scheduler/status"}, status_code=202)


app.include_router(api)
