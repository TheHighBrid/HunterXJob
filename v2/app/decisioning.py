from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


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


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _normalize(value: str) -> str:
    return " ".join(_WORD_RE.findall(value.lower()))


def _contains_any(value: str, candidates: Iterable[str]) -> bool:
    normalized = _normalize(value)
    if not normalized:
        return False
    return any(
        normalized_candidate in normalized
        for candidate in candidates
        if (normalized_candidate := _normalize(candidate))
    )


def _keyword_matches(text: str, keywords: Iterable[str]) -> tuple[str, ...]:
    normalized = _normalize(text)
    if not normalized:
        return ()
    return tuple(
        dict.fromkeys(
            keyword
            for keyword in keywords
            if keyword.strip() and (normalized_keyword := _normalize(keyword)) and normalized_keyword in normalized
        )
    )


def _bounded_score(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 2)


def _hard_veto(job: JobFacts, context: DecisionContext) -> tuple[DecisionReport | None, bool]:
    """Apply hard vetoes. Returns (veto report or None, whether the location is a named target)."""
    location = job.location or ""
    if _contains_any(job.company or "", context.blacklisted_companies):
        return DecisionReport(Decision.REJECT, "blacklisted company", 0.0, vetoes=("blacklisted_company",)), False
    if _contains_any(job.title or "", context.excluded_titles):
        return DecisionReport(Decision.REJECT, "excluded title", 0.0, vetoes=("excluded_title",)), False

    excluded_location = _contains_any(location, context.excluded_locations)
    target_location = _contains_any(location, context.target_locations)
    remote_targeted = job.remote and any(
        "remote" in _normalize(item) for item in context.target_locations if _normalize(item)
    )
    if excluded_location and not target_location:
        return DecisionReport(Decision.REJECT, "excluded location", 0.0, vetoes=("excluded_location",)), target_location
    if not target_location and not remote_targeted:
        return DecisionReport(Decision.REJECT, "location not eligible", 0.0, vetoes=("location_not_eligible",)), target_location
    return None, target_location


_SECTOR_TERMS = ("bank", "banking", "financial", "fintech", "payments", "credit", "fraud", "aml", "kyc", "compliance")
_LANGUAGE_TERMS = ("bilingual", "french", "français", "english and french", "fr/en")


def _seniority(title: str) -> tuple[float, list[str]]:
    if _contains_any(title, ("director", "vice president", "vp", "head of", "principal")):
        return 1.5, ["seniority may exceed target level"]
    if _contains_any(title, ("manager", "lead", "senior")):
        return 3.0, []
    return 4.5, []


def _dimensions(
    job: JobFacts, context: DecisionContext, matched_keywords: tuple[str, ...], target_location: bool
) -> tuple[tuple[DimensionScore, ...], list[str]]:
    """Score the six dimensions. Returns (dimensions, review flags)."""
    description = job.description or ""
    combined = f"{job.title or ''}\n{description}\n{job.company or ''}\n{job.location or ''}"

    keyword_ratio = len(matched_keywords) / max(1, min(6, len(context.target_keywords)))
    sector_hits = _keyword_matches(combined, _SECTOR_TERMS)
    language_hits = _keyword_matches(combined, _LANGUAGE_TERMS)
    seniority_score, seniority_flags = _seniority(job.title or "")
    description_words = len(_WORD_RE.findall(description))

    review_flags = list(seniority_flags)
    if description_words < 25:
        review_flags.append("job description is too thin for a confident decision")

    dimensions = (
        DimensionScore("role_relevance", _bounded_score(2.5 + keyword_ratio * 3.0), 30.0, matched_keywords),
        DimensionScore("location_fit", 5.0 if target_location else 4.0, 20.0, (job.location or "",)),
        DimensionScore("sector_fit", _bounded_score(2.0 + min(3.0, len(sector_hits) * 0.8)), 15.0, sector_hits),
        DimensionScore("language_fit", 5.0 if language_hits else 2.5, 10.0, language_hits),
        DimensionScore("seniority_fit", seniority_score, 10.0, tuple(seniority_flags)),
        DimensionScore(
            "evidence_quality",
            _bounded_score(1.5 + min(3.5, description_words / 90.0)),
            15.0,
            (f"{description_words} description words",),
        ),
    )
    return dimensions, review_flags


def _decide(total: float, review_flags: list[str], threshold: float) -> tuple[Decision, str]:
    if review_flags:
        return Decision.REVIEW, "eligible with review flags"
    if total >= threshold:
        return Decision.SHORTLIST, "passed deterministic eligibility"
    return Decision.REVIEW, "below shortlist threshold"


def evaluate_job(job: JobFacts, context: DecisionContext) -> DecisionReport:
    """Evaluate a normalized job without an LLM.

    Hard vetoes run first. Remaining jobs receive a transparent six-dimension
    score so callers can explain why a role advanced instead of exposing only
    an opaque number.
    """
    veto, target_location = _hard_veto(job, context)
    if veto is not None:
        return veto

    matched_keywords = _keyword_matches(f"{job.title or ''}\n{job.description or ''}", context.target_keywords)
    if not matched_keywords:
        return DecisionReport(Decision.REJECT, "no target-role overlap", 0.0, vetoes=("no_role_overlap",))

    dimensions, review_flags = _dimensions(job, context, matched_keywords, target_location)
    total = round(sum(item.weighted_points for item in dimensions), 2)
    decision, reason = _decide(total, review_flags, context.shortlist_threshold)
    return DecisionReport(
        decision=decision,
        reason=reason,
        score=total,
        dimensions=dimensions,
        matched_keywords=matched_keywords,
        review_flags=tuple(review_flags),
    )
