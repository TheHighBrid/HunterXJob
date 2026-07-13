from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import LocalAI
from app.config import get_settings
from app.db import get_db, init_db
from app.discovery import discover_all, upsert_jobs
from app.models import Application, Job, PipelineEvent
from app.pipeline import generate_materials, score_pending_jobs

settings = get_settings()
RESUME_FACTS_PATH = Path("data/resume_facts.txt")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    yield


app = FastAPI(title="HunterXJob v2", version="0.1.0", lifespan=lifespan)


class ResumeFactsIn(BaseModel):
    text: str


def read_resume_facts() -> str:
    if not RESUME_FACTS_PATH.exists():
        return ""
    return RESUME_FACTS_PATH.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>HunterXJob v2</title><style>body{font-family:system-ui;background:#111;color:#eee;max-width:900px;margin:auto;padding:24px}button{padding:14px;margin:6px;background:#eee;border:0;border-radius:8px}pre{white-space:pre-wrap;background:#1d1d1d;padding:16px;border-radius:10px}</style></head><body><h1>HunterXJob v2</h1><p>Local-first, zero-cost, durable job automation.</p><button onclick="run('/api/discovery/run')">Discover</button><button onclick="run('/api/scoring/run')">Score</button><button onclick="load('/api/jobs')">Jobs</button><button onclick="load('/api/applications')">Applications</button><button onclick="load('/api/health')">Health</button><pre id='out'>Ready.</pre><script>async function run(u){out.textContent='Working...';let r=await fetch(u,{method:'POST'});out.textContent=JSON.stringify(await r.json(),null,2)}async function load(u){let r=await fetch(u);out.textContent=JSON.stringify(await r.json(),null,2)}</script></body></html>"""


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "mode": settings.application_mode,
        "automation_enabled": settings.automation_enabled,
        "ai": LocalAI(settings).health(),
        "resume_loaded": RESUME_FACTS_PATH.exists(),
    }


@app.put("/api/resume-facts")
def save_resume_facts(payload: ResumeFactsIn) -> dict[str, object]:
    RESUME_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESUME_FACTS_PATH.write_text(payload.text.strip(), encoding="utf-8")
    return {"saved": True, "characters": len(payload.text.strip())}


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
        "reason": j.eligibility_reason,
    } for j in rows]


@app.get("/api/applications")
def list_applications(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.execute(select(Application).order_by(Application.created_at.desc())).scalars()
    return [{
        "id": a.id, "job_id": a.job_id, "stage": a.stage, "mode": a.mode,
        "cover_letter_text": a.cover_letter_text, "last_error": a.last_error,
        "attempts": a.attempts,
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


@app.get("/api/events/{job_id}")
def events(job_id: str, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job_id).order_by(PipelineEvent.created_at)).scalars()
    return [{"from": e.from_stage, "to": e.to_stage, "message": e.message, "created_at": e.created_at} for e in rows]
