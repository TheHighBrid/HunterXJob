from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapter_runtime import ADAPTER_CATALOG
from app.ai import LocalAI
from app.config import get_settings
from app.db import get_db, init_db
from app.discovery import discover_all, upsert_jobs
from app.flags import ensure_flags, set_flag, snapshot
from app.models import Application, Job, PipelineEvent, ReviewTask
from app.pipeline import approve_application, execute_apply, generate_materials, score_pending_jobs
from app.review_queue import list_open, resolve_task
from app.scheduler import can_run_unattended, submissions_today
from app.vault_store import upsert_answer

settings = get_settings()
RESUME_FACTS_PATH = Path("data/resume_facts.txt")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        ensure_flags(db)
    finally:
        db.close()
    yield


app = FastAPI(title="HunterXJob v2", version="0.2.0", lifespan=lifespan)


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


def read_resume_facts() -> str:
    if not RESUME_FACTS_PATH.exists():
        return ""
    return RESUME_FACTS_PATH.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>HunterXJob v2</title>
<style>body{font-family:system-ui;background:#111;color:#eee;max-width:960px;margin:auto;padding:24px}button{padding:12px;margin:4px;background:#eee;color:#111;border:0;border-radius:8px}pre{white-space:pre-wrap;background:#1d1d1d;padding:16px;border-radius:10px}</style></head>
<body><h1>HunterXJob v2</h1><p>Bounded autonomy. Dry-run by default. Live submit stays locked until an adapter is certified.</p>
<button onclick=\"run('/api/discovery/run')\">Discover</button>
<button onclick=\"run('/api/scoring/run')\">Score</button>
<button onclick=\"load('/api/jobs')\">Jobs</button>
<button onclick=\"load('/api/applications')\">Applications</button>
<button onclick=\"load('/api/review-tasks')\">Review queue</button>
<button onclick=\"load('/api/adapters')\">Adapters</button>
<button onclick=\"load('/api/flags')\">Flags</button>
<button onclick=\"load('/api/health')\">Health</button>
<pre id='out'>Ready.</pre>
<script>
async function run(u){out.textContent='Working...';let r=await fetch(u,{method:'POST'});out.textContent=JSON.stringify(await r.json(),null,2)}
async function load(u){let r=await fetch(u);out.textContent=JSON.stringify(await r.json(),null,2)}
</script></body></html>"""


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    flags = snapshot(db)
    decision = can_run_unattended(db, settings)
    return {
        "ok": True,
        "version": "0.2.0",
        "mode": settings.application_mode,
        "automation_enabled": settings.automation_enabled,
        "allow_live_submission": settings.allow_live_submission,
        "unattended_allowed": decision.allowed,
        "unattended_reason": decision.reason,
        "submissions_today": submissions_today(db),
        "open_review_tasks": db.execute(select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")).scalar_one(),
        "flags": flags,
        "ai": LocalAI(settings).health(),
        "resume_loaded": RESUME_FACTS_PATH.exists(),
    }


@app.put("/api/resume-facts")
def save_resume_facts(payload: ResumeFactsIn) -> dict[str, object]:
    RESUME_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESUME_FACTS_PATH.write_text(payload.text.strip(), encoding="utf-8")
    return {"saved": True, "characters": len(payload.text.strip())}


@app.post("/api/answers")
def save_answer(payload: AnswerIn, db: Session = Depends(get_db)) -> dict[str, object]:
    row = upsert_answer(db, **payload.model_dump())
    return {"id": row.id, "key": row.key, "scope": row.scope, "source": row.source}


@app.post("/api/discovery/run")
def run_discovery(db: Session = Depends(get_db)) -> dict[str, int]:
    records = discover_all(settings)
    added = upsert_jobs(db, records)
    return {"discovered": len(records), "added": added}


@app.post("/api/scoring/run")
def run_scoring(db: Session = Depends(get_db)) -> dict[str, int]:
    resume = read_resume_facts()
    if not resume:
        raise HTTPException(409, "resume facts are not configured")
    return {"processed": score_pending_jobs(db, settings, resume, use_ai=True)}


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db), limit: int = 100) -> list[dict[str, object]]:
    rows = db.execute(select(Job).order_by(Job.final_score.desc().nullslast()).limit(min(limit, 500))).scalars()
    return [{
        "id": j.id, "title": j.title, "company": j.company, "location": j.location,
        "stage": j.stage, "eligible": j.eligible, "score": j.final_score, "url": j.url,
        "reason": j.eligibility_reason, "platform": j.platform,
    } for j in rows]


@app.get("/api/applications")
def list_applications(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.execute(select(Application).order_by(Application.created_at.desc())).scalars()
    return [{
        "id": a.id, "job_id": a.job_id, "stage": a.stage, "mode": a.mode,
        "cover_letter_text": a.cover_letter_text, "last_error": a.last_error,
        "attempts": a.attempts, "adapter": a.adapter_name, "maturity": a.adapter_maturity,
        "idempotency_key": a.idempotency_key,
    } for a in rows]


@app.post("/api/applications/{application_id}/generate")
def generate(application_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    resume = read_resume_facts()
    if not resume:
        raise HTTPException(409, "resume facts are not configured")
    try:
        result = generate_materials(db, settings, application_id, resume)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": result.id, "stage": result.stage, "last_error": result.last_error}


@app.post("/api/applications/{application_id}/approve")
def approve(application_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        result = approve_application(db, application_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": result.id, "stage": result.stage, "job_stage": result.job.stage}


@app.post("/api/applications/{application_id}/apply")
def apply(application_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        return execute_apply(db, settings, application_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/review-tasks")
def review_tasks(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return [{
        "id": t.id, "reason_code": t.reason_code, "status": t.status,
        "title": t.title, "detail": t.detail, "url": t.url,
        "application_id": t.application_id, "job_id": t.job_id,
    } for t in list_open(db)]


@app.post("/api/review-tasks/{task_id}/resolve")
def resolve_review(task_id: str, payload: ReviewResolveIn, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        task = resolve_task(db, task_id, payload.resolution)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": task.id, "status": task.status}


@app.get("/api/adapters")
def adapters() -> list[dict[str, object]]:
    return [{
        "name": info.name,
        "maturity": info.maturity.value,
        "feature_flag": info.feature_flag,
        "notes": info.notes,
        "version": info.version,
    } for info in ADAPTER_CATALOG.values()]


@app.get("/api/flags")
def flags(db: Session = Depends(get_db)) -> dict[str, bool]:
    return snapshot(db)


@app.put("/api/flags/{key}")
def update_flag(key: str, payload: FlagIn, db: Session = Depends(get_db)) -> dict[str, object]:
    flag = set_flag(db, key, payload.enabled, payload.note)
    return {"key": flag.key, "enabled": flag.enabled, "note": flag.note}


@app.get("/api/events/{job_id}")
def events(job_id: str, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job_id).order_by(PipelineEvent.created_at)).scalars()
    return [{"from": e.from_stage, "to": e.to_stage, "message": e.message, "created_at": e.created_at} for e in rows]
