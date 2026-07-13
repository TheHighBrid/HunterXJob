from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import HealthOut
from app.services.browser_runtime import probe_browser
from app.services.runtime_settings import load_runtime_settings
from app.services.scheduler import get_scheduler_status

router = APIRouter(prefix="/api", tags=["health"])

APP_VERSION = "0.1.0"


@router.get("/health", response_model=HealthOut)
def health(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> HealthOut:
    """Unauthenticated liveness check for monitoring and mobile setup."""
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


@router.get("/browser/health")
def browser_health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Probe native Chromium/CDP without launching or terminating a browser."""
    return probe_browser(settings)
