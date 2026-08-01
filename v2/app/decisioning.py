from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable


class Decision(str, Enum):
    REJECT = "reject"
    REVIEW = "review"
    SHORTLIST = "shortlist"


@dataclass(frozen=True, slots=True)
class JobFacts:
    title: str
    company: str
    location: str
    description: str
    remote: bool = False
    url: str = ""


@dataclass(frozen=True, slots=True)
class DecisionContext:
    target_locations: tuple[str, ...] = ()
    target_keywords: tuple[str, ...] = ()
    excluded_locations: tuple[str, ...] = ()
    excluded_titles: tuple[str, ...] = ()
    blacklisted_companies: tuple[str, ...] = ()
    shortlist_threshold: float = 60.0


@dataclass(frozen=True, slots=True)
class DimensionScore:
    name: str
    score: float
    weight: float
    evidence: tuple[str, ...] = ()

    @property
    def weighted_points(self) -> float:
        return round((self.score / 5.0) * self.weight, 2)


@dataclass(frozen=True, slots=True)
class DecisionReport:
    decision: Decision
    reason: str
    score: float
    dimensions: tuple[DimensionScore, ...] = ()
    matched_keywords: tuple[str, ...] = ()
    review_flags: tuple[str, ...] = ()
    vetoes: tuple[str, ...] = ()


_WORD_RE = re.compile(r"[a-z0-9+#.-]+", re.IGNORECASE)


def _normalize(value: str) -> str:
    return " ".join(_WORD_RE.findall(value.lower()))


def _contains_any(value: str, candidates: Iterable[str]) -> bool:
    normalized = _normalize(value)
    return any(_normalize(candidate) in normalized for candidate in candidates if candidate.strip())


def _keyword_matches(text: str, keywords: Iterable[str]) -> tuple[str, ...]:
    normalized = _normalize(text)
    return tuple(dict.fromkeys(keyword for keyword in keywords if keyword.strip() and _normalize(keyword) in normalized))


def _bounded_score(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 2)


def evaluate_job(job: JobFacts, context: DecisionContext) -> DecisionReport:
    """Evaluate a normalized job without an LLM.

    Hard vetoes run first. Remaining jobs receive a transparent six-dimension
    score so callers can explain why a role advanced instead of exposing only
    an opaque number.
    """

    title = job.title or ""
    company = job.company or ""
    location = job.location or ""
    description = job.description or ""
    combined = f"{title}\n{company}\n{location}\n{description}"

    if _contains_any(company, context.blacklisted_companies):
        return DecisionReport(Decision.REJECT, "blacklisted company", 0.0, vetoes=("blacklisted_company",))
    if _contains_any(title, context.excluded_titles):
        return DecisionReport(Decision.REJECT, "excluded title", 0.0, vetoes=("excluded_title",))

    excluded_location = _contains_any(location, context.excluded_locations)
    target_location = _contains_any(location, context.target_locations)
    remote_targeted = job.remote and any("remote" in _normalize(item) for item in context.target_locations)
    if excluded_location and not target_location:
        return DecisionReport(Decision.REJECT, "excluded location", 0.0, vetoes=("excluded_location",))
    if not target_location and not remote_targeted:
        return DecisionReport(Decision.REJECT, "location not eligible", 0.0, vetoes=("location_not_eligible",))

    matched_keywords = _keyword_matches(combined, context.target_keywords)
    if not matched_keywords:
        return DecisionReport(Decision.REJECT, "no target-role overlap", 0.0, vetoes=("no_role_overlap",))

    keyword_ratio = len(matched_keywords) / max(1, min(6, len(context.target_keywords)))
    role_score = _bounded_score(2.5 + keyword_ratio * 3.0)
    location_score = 5.0 if target_location else 4.0

    sector_terms = ("bank", "banking", "financial", "fintech", "payments", "credit", "fraud", "aml", "kyc", "compliance")
    sector_hits = _keyword_matches(combined, sector_terms)
    sector_score = _bounded_score(2.0 + min(3.0, len(sector_hits) * 0.8))

    language_terms = ("bilingual", "french", "français", "english and french", "fr/en")
    language_hits = _keyword_matches(combined, language_terms)
    language_score = 5.0 if language_hits else 2.5

    seniority_flags: list[str] = []
    if _contains_any(title, ("director", "vice president", "vp", "head of", "principal")):
        seniority_score = 1.5
        seniority_flags.append("seniority may exceed target level")
    elif _contains_any(title, ("manager", "lead", "senior")):
        seniority_score = 3.0
    else:
        seniority_score = 4.5

    description_words = len(_WORD_RE.findall(description))
    evidence_score = _bounded_score(1.5 + min(3.5, description_words / 90.0))
    review_flags = list(seniority_flags)
    if description_words < 25:
        review_flags.append("job description is too thin for a confident decision")

    dimensions = (
        DimensionScore("role_relevance", role_score, 30.0, matched_keywords),
        DimensionScore("location_fit", location_score, 20.0, (location,)),
        DimensionScore("sector_fit", sector_score, 15.0, sector_hits),
        DimensionScore("language_fit", language_score, 10.0, language_hits),
        DimensionScore("seniority_fit", seniority_score, 10.0, tuple(seniority_flags)),
        DimensionScore("evidence_quality", evidence_score, 15.0, (f"{description_words} description words",)),
    )
    total = round(sum(item.weighted_points for item in dimensions), 2)
    decision = Decision.SHORTLIST if total >= context.shortlist_threshold and not review_flags else Decision.REVIEW
    reason = "passed deterministic eligibility" if decision is Decision.SHORTLIST else "eligible with review flags"
    return DecisionReport(
        decision=decision,
        reason=reason,
        score=total,
        dimensions=dimensions,
        matched_keywords=matched_keywords,
        review_flags=tuple(review_flags),
    )
