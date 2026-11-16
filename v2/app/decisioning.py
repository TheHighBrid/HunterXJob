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


_OTTAWA_NCR_TERMS = (
    "ottawa", "gatineau", "national capital region", "kanata", "nepean", "gloucester", "hull", "orleans"
)
_REMOTE_TERMS = ("remote", "telework", "work from home", "wfh", "anywhere")
_HYBRID_ON_SITE_TERMS = ("hybrid", "on-site", "onsite", "in-person", "in office", "office-based")

_CANADA_GEO_RE = re.compile(
    r"\b(?:canada|ontario|quebec|québec|british\s+columbia|alberta|manitoba|saskatchewan|"
    r"nova\s+scotia|new\s+brunswick|newfoundland|prince\s+edward\s+island|"
    r"ottawa|gatineau|toronto|montreal|montréal|vancouver|calgary|edmonton|winnipeg|"
    r"waterloo|kitchener|mississauga|brampton|halifax|victoria)\b"
    r"|(?:^|[,\s(])(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE)(?:$|[,\s)])",
    re.I,
)
_FOREIGN_GEO_RE = re.compile(
    r"\b(?:united\s+states|u\.s\.a?\.?|usa|san\s+francisco|new\s+york|nyc|california|texas|"
    r"seattle|chicago|boston|austin|denver|atlanta|miami|los\s+angeles|united\s+kingdom|"
    r"great\s+britain|england|london,\s*uk|ireland|dublin|germany|berlin|france|paris|"
    r"singapore|india|bangalore|bengaluru|australia|sydney|melbourne|mexico|brazil)\b"
    r"|(?:^|[,\s(-])U\.?S\.?(?:$|[,\s)-])",
    re.I,
)

# Role families clearly outside Mohamed's financial operations, fraud/AML, collections,
# disputes, risk/compliance, and bilingual customer/banking support background.
_NON_TARGET_ROLE_PATTERNS = (
    re.compile(
        r"\b(?:software|full[- ]?stack|front[- ]?end|back[- ]?end|web|mobile|ios|android|cloud|"
        r"devops|sre|platform|infrastructure|systems?|network|embedded|firmware|cybersecurity|"
        r"application|qa|quality\s+assurance|test|sdet|machine\s+learning|ml|ai|llm)\s+"
        r"(?:engineer(?:ing)?|developer|architect|programmer|scientist)\b",
        re.I,
    ),
    re.compile(r"\bfull[- ]?stack\s+development\b|\bsite\s+reliability\b", re.I),
    re.compile(
        r"\b(?:staff|principal|distinguished|fellow)\s+(?:software\s+)?(?:engineer|developer|architect|designer)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:product|technical\s+product|product\s+growth)\s+(?:manager|management|owner|lead|director|specialist)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:product|ux|ui|graphic|visual|brand|motion|web)\s+(?:designer|design|researcher)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:marketing|public\s+relations|copywriter|content\s+writer|seo|demand\s+gen|"
        r"brand|growth\s+marketing|product\s+marketing|lifecycle\s+marketing)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:account\s+executive|business\s+development|sales\s+development|sales\s+engineer|"
        r"sales\s+representative|shopping\s+centre|solutions\s+architect|solutions\s+engineer|"
        r"revenue\s+enablement|sales\s+enablement|operations\s+enablement|bdr|sdr)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:recruiter|talent\s+acquisition|people\s+partner|people\s+operations|hris|human\s+resources|"
        r"general\s+counsel|legal\s+counsel|paralegal|attorney)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:strategic\s+finance|fp&a|financial\s+planning|corporate\s+accounting|accounting\s+manager|"
        r"gl\s+operations|intercompany|tax\s+(?:manager|analyst|accountant)|payroll\s+(?:manager|specialist|administrator)|"
        r"it\s+operations|security\s+risk|financial\s+model\s+risk|"
        r"product\s+support\s+manager|manager,?\s+product\s+support)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:chief\s+\w+\s+officer|ceo|cto|cfo|coo|cpo|cro|cmo|ciso|svp|evp|vice\s+president)\b",
        re.I,
    ),
)

_TITLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "fraud": ("fincrime", "financial crime", "fraud", "scam", "trust & safety", "trust and safety"),
    "aml": ("kyc", "kyb", "edd", "cdd", "sanctions", "fintrac", "anti-money laundering", "financial crime", "fincrime"),
    "kyc": ("aml", "kyb", "edd", "cdd", "onboarding", "verification", "compliance"),
    "compliance": ("aml", "kyc", "regulatory", "fintrac", "sanctions", "governance", "risk"),
    "collections": ("collection", "recovery", "accounts receivable", "default", "delinquency", "past due"),
    "credit": ("lending", "underwriting", "adjudication", "loan", "collections", "risk"),
    "loan": ("lending", "credit", "underwriting", "mortgage", "adjudication", "collections"),
    "disputes": ("dispute", "chargeback", "chargebacks", "claims", "escalation", "resolution"),
    "chargeback": ("disputes", "dispute", "claims", "fraud"),
    "risk": ("fraud", "aml", "kyc", "compliance", "credit", "trust"),
    "customer support": ("customer care", "customer experience", "customer service", "client support", "client care", "member support", "member experience"),
    "customer service": ("customer care", "customer experience", "customer support", "client service", "client care"),
    "customer operations": ("client operations", "member operations", "banking operations", "payment operations", "operations associate", "operations specialist"),
    "bilingual": ("french", "français", "fr/en", "en/fr"),
    "french": ("bilingual", "français", "fr/en", "en/fr"),
}

_MANDATORY_CPA_PATTERNS = (
    re.compile(r"\bcpa\s+(?:designation\s+)?(?:is\s+)?required\b", re.I),
    re.compile(r"\b(?:must|should)\s+(?:hold|have|possess)\s+(?:an?\s+)?(?:active\s+)?cpa\b", re.I),
    re.compile(r"\bactive\s+cpa\b", re.I),
    re.compile(r"\bholding\s+(?:a\s+)?cpa\b", re.I),
    re.compile(r"\bdesignated\s+cpa\b", re.I),
    re.compile(r"\bcpa\s+(?:or\s+equivalent)\s+required\b", re.I),
    re.compile(r"\bcpa\s+designation\s+\(or\s+equivalent\)\b", re.I),
)


def _title_alignment_matches(title: str, target_keywords: tuple[str, ...]) -> tuple[str, ...]:
    """Match job title against target keywords and strictly related domain synonyms."""
    direct = _keyword_matches(title, target_keywords)
    if direct:
        return direct
    if len(target_keywords) <= 1:
        return ()

    norm_title = _normalize(title)
    expanded: list[str] = []
    seen: set[str] = set()
    for kw in target_keywords:
        for syn in _TITLE_SYNONYMS.get(_normalize(kw), ()):
            if syn not in seen and _contains_any(norm_title, (syn,)):
                seen.add(syn)
                expanded.append(syn)
    return tuple(expanded)


def _hard_veto(job: JobFacts, context: DecisionContext) -> tuple[DecisionReport | None, bool]:
    """Apply hard vetoes. Returns (veto report or None, whether the location is a named target)."""
    location = job.location or ""
    norm_location = _normalize(location)
    norm_title = _normalize(job.title or "")
    desc_sample = (job.description or "")[:800].lower()

    if _contains_any(job.company or "", context.blacklisted_companies):
        return DecisionReport(Decision.REJECT, "blacklisted company", 0.0, vetoes=("blacklisted_company",)), False
    if _contains_any(job.title or "", context.excluded_titles):
        return DecisionReport(Decision.REJECT, "excluded title", 0.0, vetoes=("excluded_title",)), False
    if any(p.search(job.title or "") for p in _NON_TARGET_ROLE_PATTERNS):
        return DecisionReport(
            Decision.REJECT,
            f"non-target role domain: {job.title}",
            0.0,
            vetoes=("excluded_title",),
        ), False

    # 1. Truth Guard Veto: Mandatory qualifications not held in profile (e.g. CPA required)
    desc_full = (job.description or "").lower()
    if any(p.search(desc_full) for p in _MANDATORY_CPA_PATTERNS) or (
        "cpa" in norm_title and any(w in norm_title for w in ("manager", "accountant", "controller", "internal controls", "audit"))
    ):
        return DecisionReport(
            Decision.REJECT,
            "mandatory CPA designation required but not verified in candidate profile",
            0.0,
            vetoes=("unverified_mandatory_credential",),
        ), False

    # 2. Location & Workplace Type Analysis
    explicit_canada = bool(_CANADA_GEO_RE.search(location) or "canada" in norm_title)
    excluded_location = _contains_any(location, context.excluded_locations)
    is_remote = (
        job.remote
        or any(term in norm_location or term in norm_title for term in _REMOTE_TERMS)
        or "100% remote" in desc_sample
        or "fully remote" in desc_sample
    )
    target_location = _contains_any(location, context.target_locations) or (explicit_canada and is_remote)
    if excluded_location and not target_location:
        return DecisionReport(Decision.REJECT, "excluded location", 0.0, vetoes=("excluded_location",)), target_location

    explicit_foreign = bool(_FOREIGN_GEO_RE.search(location)) and not explicit_canada
    if explicit_foreign:
        return DecisionReport(
            Decision.REJECT,
            f"non-Canadian location: {location}",
            0.0,
            vetoes=("international_non_canada",),
        ), False

    is_ottawa_ncr = any(term in norm_location for term in _OTTAWA_NCR_TERMS)
    location_hybrid = any(term in norm_location or term in norm_title for term in _HYBRID_ON_SITE_TERMS)
    desc_hybrid = any(term in desc_sample[:300] for term in _HYBRID_ON_SITE_TERMS) and not is_remote
    is_explicit_hybrid_or_onsite = location_hybrid or desc_hybrid

    # Check if a non-Ottawa location was explicitly targeted as a specific city (e.g. "Montréal")
    explicit_city_target = any(
        _normalize(target) in norm_location
        for target in context.target_locations
        if _normalize(target) not in ("canada", "remote canada", "remote", "anywhere")
    )

    # Candidate rule: In-person or hybrid work is allowed ONLY in Ottawa–Gatineau (or explicitly targeted city).
    # All other Canadian locations must be remote.
    if (not is_remote or is_explicit_hybrid_or_onsite) and not is_ottawa_ncr and not explicit_city_target and location:
        return DecisionReport(
            Decision.REJECT,
            f"non-remote or hybrid position outside Ottawa-Gatineau: {location}",
            0.0,
            vetoes=("non_remote_outside_ottawa",),
        ), False

    remote_targeted = is_remote and (explicit_canada or norm_location in ("", "remote", "anywhere")) and any(
        "remote" in _normalize(item) for item in context.target_locations if _normalize(item)
    )
    if not target_location and not remote_targeted:
        return DecisionReport(Decision.REJECT, "location not eligible", 0.0, vetoes=("location_not_eligible",)), target_location
    return None, target_location


_SECTOR_TERMS = ("bank", "banking", "financial", "fintech", "payments", "credit", "fraud", "aml", "kyc", "compliance")
_LANGUAGE_TERMS = ("bilingual", "french", "français", "english and french", "fr/en", "en/fr")
_IC_TITLE_TERMS = ("analyst", "officer", "specialist", "associate", "coordinator", "representative", "advisor", "investigator")


def _seniority(title: str) -> tuple[float, list[str]]:
    if _contains_any(title, ("director", "vice president", "vp", "head of", "principal")):
        return 1.5, ["seniority may exceed target level"]
    if _contains_any(title, ("manager", "lead")) and not _contains_any(title, _IC_TITLE_TERMS):
        return 2.5, []
    return 4.5, []


def _dimensions(
    job: JobFacts,
    context: DecisionContext,
    matched_keywords: tuple[str, ...],
    title_keywords: tuple[str, ...],
    target_location: bool,
) -> tuple[tuple[DimensionScore, ...], list[str]]:
    """Score the six dimensions. Returns (dimensions, review flags)."""
    description = job.description or ""
    combined = f"{job.title or ''}\n{description}\n{job.company or ''}\n{job.location or ''}"

    title_score = min(3.2, 2.0 + 0.6 * max(0, len(title_keywords) - 1)) if title_keywords else 0.0
    desc_ratio = len(matched_keywords) / max(1, min(5, len(context.target_keywords)))
    role_score = _bounded_score(title_score + desc_ratio * 1.8)

    sector_hits = _keyword_matches(combined, _SECTOR_TERMS)
    language_hits = _keyword_matches(combined, _LANGUAGE_TERMS)
    seniority_score, seniority_flags = _seniority(job.title or "")
    description_words = len(_WORD_RE.findall(description))

    review_flags = list(seniority_flags)
    if description_words < 25:
        review_flags.append("job description is too thin for a confident decision")

    dimensions = (
        DimensionScore("role_relevance", role_score, 45.0, title_keywords or matched_keywords),
        DimensionScore("location_fit", 5.0 if target_location else 4.0, 20.0, (job.location or "",)),
        DimensionScore("sector_fit", _bounded_score(1.0 + min(4.0, len(sector_hits) * 0.8)), 15.0, sector_hits),
        DimensionScore("language_fit", 5.0 if language_hits else 3.0, 10.0, language_hits),
        DimensionScore("seniority_fit", seniority_score, 5.0, tuple(seniority_flags)),
        DimensionScore(
            "evidence_quality",
            _bounded_score(1.0 + min(4.0, description_words / 75.0)),
            5.0,
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

    title_keywords = _title_alignment_matches(job.title or "", context.target_keywords)
    if not title_keywords:
        return DecisionReport(
            Decision.REJECT,
            f"title lacks target-role alignment: {job.title}",
            0.0,
            vetoes=("no_role_overlap",),
        )

    matched_keywords = _keyword_matches(f"{job.title or ''}\n{job.description or ''}", context.target_keywords)
    if not matched_keywords:
        matched_keywords = title_keywords

    dimensions, review_flags = _dimensions(job, context, matched_keywords, title_keywords, target_location)
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
