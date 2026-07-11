from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.db import get_db
from app.models import AutomationRun
from app.schemas import AutomationRunOut, AutomationRunTriggerResponse
from app.services.scheduler import automation_cycle

router = APIRouter(
    prefix="/api/automation", tags=["automation"], dependencies=[Depends(require_api_key)]
)


@router.post("/run", response_model=AutomationRunTriggerResponse, status_code=202)
def trigger_automation_run(background_tasks: BackgroundTasks) -> AutomationRunTriggerResponse:
    """Trigger an automation cycle now. Runs in a background task and is
    subject to the same safety rails (daily cap, delay, blacklist) as the
    scheduled cycle."""
    background_tasks.add_task(automation_cycle)
    return AutomationRunTriggerResponse(
        message="automation cycle triggered in background", run_id=None
    )


@router.get("/runs", response_model=list[AutomationRunOut])
def list_automation_runs(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[AutomationRun]:
    stmt = (
        select(AutomationRun)
        .order_by(AutomationRun.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
