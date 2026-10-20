from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.db import get_db
from app.models import JobPosting
from app.schemas import JobPostingOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[JobPostingOut])
def list_jobs(
    db: Session = Depends(get_db),
    min_score: float | None = Query(None, ge=0, le=100),
    source: str | None = None,
    company: str | None = None,
    remote: bool | None = None,
    sort_by: str = Query("match_score", pattern="^(match_score|discovered_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[JobPosting]:
    stmt = select(JobPosting)
    if min_score is not None:
        stmt = stmt.where(JobPosting.match_score >= min_score)
    if source:
        stmt = stmt.where(JobPosting.source == source)
    if company:
        stmt = stmt.where(JobPosting.company.ilike(f"%{company}%"))
    if remote is not None:
        stmt = stmt.where(JobPosting.remote == remote)

    sort_col = JobPosting.match_score if sort_by == "match_score" else JobPosting.discovered_at
    stmt = stmt.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    stmt = stmt.offset(offset).limit(limit)

    return list(db.execute(stmt).scalars().all())
