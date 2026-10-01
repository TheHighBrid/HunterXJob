from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.models import AnswerPolicy
from app.profile import verified_profile
from app.profile_vault import profile_records


def load_vault(db: Session, scope: str | Iterable[str] = "global", *, profile: bool = True) -> AnswerVault:
    """Load answers for ``global`` plus the given scope(s).

    Verified profile facts (contact, current role, work authorization by
    country, employment/education history) come first; stored answers then
    override them. Scopes are applied from least to most specific, so an
    employer- or job-scoped answer overrides a global answer with the same key.
    """
    scopes = [scope] if isinstance(scope, str) else list(scope)
    ordered = ["global", *[item for item in scopes if item != "global"]]
    rows = list(db.execute(select(AnswerPolicy).where(AnswerPolicy.scope.in_(ordered))).scalars())
    rank = {name: index for index, name in enumerate(ordered)}
    rows.sort(key=lambda row: rank.get(row.scope, 0))
    records: list[AnswerRecord] = []
    if profile:
        records.extend(profile_records(verified_profile(db)))
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
