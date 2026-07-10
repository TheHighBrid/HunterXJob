from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import HealthOut
from app.services.runtime_settings import load_runtime_settings
from app.services.scheduler import get_scheduler_status

router = APIRouter(prefix="/api", tags=["health"])

APP_VERSION = "0.1.0"


@router.get("/health", response_model=HealthOut)
def health(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> HealthOut:
    """Unauthenticated liveness check (used by Docker/monitoring probes, and
    by the mobile dashboard before a backend URL/API key is configured).

    Every other /api/* route requires X-API-Key per docs/ARCHITECTURE.md
    section 5; this one is deliberately left open so container health
    checks, uptime monitors, and the mobile app's first-run flow don't need
    the secret key. Nothing returned here is sensitive - operational status
    and which (non-secret) LLM provider/model is configured.
    """
    runtime = load_runtime_settings(db, settings)
    scheduler_status = get_scheduler_status()
    return HealthOut(
        status="ok",
        version=APP_VERSION,
        scheduler_running=scheduler_status["running"],
        next_scheduled_run=scheduler_status["next_automation_run"],
        llm_provider=runtime.llm_provider,
        llm_model=runtime.llm_model,
    )
