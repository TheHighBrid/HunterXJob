"""Answer-vault entries derived from *verified* profile facts.

These fill the structured parts of application forms: name/contact fields,
current company/title, per-country work authorization, and Greenhouse's
employment/education history rows. Explicit answers stored in the vault
always win over these (they are loaded first and overridden).
"""

from __future__ import annotations

import re
from typing import Any

from app.answer_vault import AnswerRecord, AnswerSource
from app.materials import chronological
from app.profile import VerifiedProfile

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")
_INDEX_RE = re.compile(r"(?:-|_|\[)(\d{1,2})\]?$")
_EDU_RE = re.compile(r"school|institution|university|college|education|degree|discipline|field of study|major")
_JOB_RE = re.compile(r"company|employer|employment|job|work|title|position")
_FIELD_RULES: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (re.compile(r"school|institution|university|college"), "school", "education"),
    (re.compile(r"degree"), "degree", "education"),
    (re.compile(r"discipline|field.of.study|major"), "discipline", "education"),
    (re.compile(r"company|employer"), "company", "employment"),
    (re.compile(r"current.?(?:role|ly)|currently work|i currently"), "current", "employment"),
    (re.compile(r"\btitle\b|title-"), "title", "employment"),
    (re.compile(r"start.*month|month.*start"), "start_month", None),
    (re.compile(r"start.*year|year.*start"), "start_year", None),
    (re.compile(r"end.*month|month.*end"), "end_month", None),
    (re.compile(r"end.*year|year.*end"), "end_year", None),
)


def history_key(key: str, label: str = "") -> str | None:
    """Map a repeated history field (e.g. ``school--0``, ``company-name-1``) to a vault key.

    Returns e.g. ``education_0_school`` or ``employment_1_title``; None when the
    row index or the section (education vs employment) is ambiguous.
    """
    match = _INDEX_RE.search(key or "")
    if not match:
        return None
    text = f"{key} {label}".lower().replace("_", "-")
    for pattern, name, section in _FIELD_RULES:
        if not pattern.search(text):
            continue
        if section is None:
            section = "education" if _EDU_RE.search(text) else "employment" if _JOB_RE.search(text) else None
        return f"{section}_{int(match.group(1))}_{name}" if section else None
    return None


def _date_parts(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    year = value[:4]
    month = _MONTHS[int(value[5:7]) - 1] if len(value) == 7 else None
    return month, year


def _history(prefix: str, index: int, data: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    out = {f"{prefix}_{index}_{target}": data.get(source) for target, source in names.items()}
    for edge in ("start", "end"):
        month, year = _date_parts(data.get(edge))
        out[f"{prefix}_{index}_{edge}_month"] = month
        out[f"{prefix}_{index}_{edge}_year"] = year
    return out


def profile_answers(profile: VerifiedProfile) -> dict[str, Any]:
    answers: dict[str, Any] = dict(profile.contact)
    jobs = chronological(profile.of("employment"))
    current = next((fact for fact in jobs if fact.data.get("current")), None)
    if current is not None:
        answers["current_company"] = current.data["employer"]
        answers["current_title"] = current.data["title"]
    for fact in profile.of("work_authorization"):
        country = fact.data["country"]
        answers[f"work_authorization_{country}"] = "Yes" if fact.data["authorized"] else "No"
        if fact.data.get("requires_sponsorship") is not None:
            answers[f"sponsorship_{country}"] = "Yes" if fact.data["requires_sponsorship"] else "No"
    for index, fact in enumerate(jobs[:10]):
        answers.update(_history("employment", index, fact.data, {"company": "employer", "title": "title"}))
        answers[f"employment_{index}_current"] = bool(fact.data.get("current"))
    for index, fact in enumerate(chronological(profile.of("education"))[:10]):
        answers.update(_history("education", index, fact.data,
                                {"school": "institution", "degree": "degree", "discipline": "field_of_study"}))
    return {key: value for key, value in answers.items() if value not in (None, "")}


def profile_records(profile: VerifiedProfile) -> list[AnswerRecord]:
    return [AnswerRecord(key=key, value=value, source=AnswerSource.USER, note="verified profile fact")
            for key, value in profile_answers(profile).items()]
