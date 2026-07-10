from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.db import get_db
from app.models import Report
from app.schemas import ReportOut

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[ReportOut])
def list_reports(
    db: Session = Depends(get_db),
    period: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[Report]:
    stmt = select(Report)
    if period:
        stmt = stmt.where(Report.period == period)
    stmt = stmt.order_by(Report.generated_at.desc()).offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/latest", response_model=ReportOut)
def latest_report(db: Session = Depends(get_db), period: str | None = None) -> Report:
    stmt = select(Report)
    if period:
        stmt = stmt.where(Report.period == period)
    stmt = stmt.order_by(Report.generated_at.desc()).limit(1)
    report = db.execute(stmt).scalars().first()
    if report is None:
        raise HTTPException(status_code=404, detail="no reports generated yet")
    return report
