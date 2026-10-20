from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.db import get_db
from app.models import Application, JobPosting
from app.schemas import ApplicationCreate, ApplicationOut, ApplicationUpdate

router = APIRouter(
    prefix="/api/applications", tags=["applications"], dependencies=[Depends(require_api_key)]
)


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    db: Session = Depends(get_db),
    status: str | None = None,
    channel: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[Application]:
    stmt = select(Application)
    if status:
        stmt = stmt.where(Application.status == status)
    if channel:
        stmt = stmt.where(Application.channel == channel)
    stmt = stmt.order_by(Application.created_at.desc()).offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)) -> Application:
    job_posting = db.get(JobPosting, payload.job_posting_id)
    if job_posting is None:
        raise HTTPException(status_code=404, detail="job_posting_id does not exist")

    application = Application(
        job_posting_id=payload.job_posting_id,
        resume_version_id=payload.resume_version_id,
        channel=payload.channel,
        status=payload.status,
        cover_letter_text=payload.cover_letter_text,
        notes=payload.notes,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(application_id: str, db: Session = Depends(get_db)) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return application


@router.patch("/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: str, payload: ApplicationUpdate, db: Session = Depends(get_db)
) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(application, field, value)

    db.add(application)
    db.commit()
    db.refresh(application)
    return application
