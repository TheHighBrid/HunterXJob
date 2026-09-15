from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.models import AnswerPolicy


def load_vault(db: Session, scope: str = "global") -> AnswerVault:
    rows = list(db.execute(select(AnswerPolicy).where(AnswerPolicy.scope.in_(["global", scope]))).scalars())
    records = []
    for row in rows:
        try:
            source = AnswerSource(row.source)
        except ValueError:
            source = AnswerSource.IMPORTED
        records.append(AnswerRecord(key=row.key, value=row.value, source=source, confidence=row.confidence, note=row.note))
    return AnswerVault(records)


def upsert_answer(
    db: Session,
    *,
    key: str,
    value: str,
    source: str = "user",
    scope: str = "global",
    confidence: float = 1.0,
    sensitive: bool = False,
    note: str = "",
) -> AnswerPolicy:
    row = db.execute(
        select(AnswerPolicy).where(AnswerPolicy.key == key, AnswerPolicy.scope == scope)
    ).scalar_one_or_none()
    if row is None:
        row = AnswerPolicy(key=key, value=value, source=source, scope=scope, confidence=confidence, sensitive=sensitive, note=note)
        db.add(row)
    else:
        row.value = value
        row.source = source
        row.confidence = confidence
        row.sensitive = sensitive
        row.note = note
    db.commit()
    db.refresh(row)
    return row
