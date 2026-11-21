from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
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
class TargetingPolicy:
    """Profile-level toggles for which role families and locations are in scope.

    ``broad`` (the default) keeps every role family aligned with the target
    keywords but ranks core individual-contributor roles highest. ``core``
    rejects leadership, data-science, sales/account-management and adjacent
    (insurance / physical surveillance) investigation roles, and keeps
    in-person work limited to Ottawa-Gatineau or explicitly targeted cities.
    """

    preset: str = "broad"
    allow_senior_leadership: bool = True
    allow_data_science: bool = True
    allow_sales_account_mgmt: bool = True
    allow_adjacent_investigations: bool = True
    allow_onsite_canada: bool = True


TARGETING_PRESETS: dict[str, TargetingPolicy] = {
    "broad": TargetingPolicy(),
    "core": TargetingPolicy(
        preset="core",
        allow_senior_leadership=False,
        allow_data_science=False,
        allow_sales_account_mgmt=False,
        allow_adjacent_investigations=False,
        allow_onsite_canada=False,
    ),
}
DEFAULT_TARGETING_PRESET = "broad"


def targeting_policy(preset: str | None = None, **overrides: bool | None) -> TargetingPolicy:
    """Build a policy from a preset name plus optional per-toggle overrides (``None`` = keep preset)."""
    name = (preset or DEFAULT_TARGETING_PRESET).strip().lower()
    if name not in TARGETING_PRESETS:
        raise ValueError(f"unknown targeting preset: {preset!r} (expected one of {sorted(TARGETING_PRESETS)})")
    changes = {key: value for key, value in overrides.items() if value is not None}
    unknown = set(changes) - set(TargetingPolicy.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown targeting toggles: {sorted(unknown)}")
    return replace(TARGETING_PRESETS[name], **changes)


@dataclass(frozen=True, slots=True)
class DecisionContext:
    target_locations: tuple[str, ...] = ()
    target_keywords: tuple[str, ...] = ()
    excluded_locations: tuple[str, ...] = ()
    excluded_titles: tuple[str, ...] = ()
    blacklisted_companies: tuple[str, ...] = ()
    shortlist_threshold: float = 60.0
    policy: TargetingPolicy = TARGETING_PRESETS[DEFAULT_TARGETING_PRESET]


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
            if keyword.strip() and (normalized_keyword := _normalize(keyword)) and _term_in(normalized, normalized_keyword)
        )
    )


def _term_in(normalized_text: str, term: str) -> bool:
    """Word-start match on normalized text, so "aml" does not hit "streamline" but "claim" hits "claims"."""
    normalized_term = _normalize(term)
    if not normalized_term or not normalized_text:
        return False
    return re.search(rf"(?<![^\W_]){re.escape(normalized_term)}", normalized_text) is not None


def _word_in(normalized_text: str, term: str) -> bool:
    """Whole-word/phrase match on normalized text, so "on" does not hit "toronto"."""
    normalized_term = _normalize(term)
    if not normalized_term or not normalized_text:
        return False
    return re.search(rf"(?<![^\W_]){re.escape(normalized_term)}(?![^\W_])", normalized_text) is not None


def _bounded_score(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 2)


_OTTAWA_NCR_TERMS = (
    "ottawa", "gatineau", "national capital region", "kanata", "nepean", "gloucester", "hull", "orleans"
)
_REMOTE_TERMS = ("remote", "telework", "work from home", "wfh", "anywhere")
_HYBRID_ON_SITE_RE = re.compile(
    r"\b(?:hybrid|on[- ]?site|in[- ]?person|in[- ]?office|office[- ]?based)\b",
    re.IGNORECASE,
)
_DESC_REMOTE_RE = re.compile(r"\b(?:100%|fully)\s+remote\b", re.IGNORECASE)
_REMOTE_NEGATION_RE = re.compile(r"\b(?:not(?:\s+a)?|isn't|aren't|no)\s+$", re.IGNORECASE)


def _has_affirmative_remote_desc(desc_sample: str) -> bool:
    for match in _DESC_REMOTE_RE.finditer(desc_sample):
        prefix = desc_sample[max(0, match.start() - 16):match.start()]
        if not _REMOTE_NEGATION_RE.search(prefix):
            return True
    return False


_CANADA_GEO_RE = re.compile(
    r"\b(?:canada|ontario|quebec|québec|british\s+columbia|alberta|manitoba|saskatchewan|"
    r"nova\s+scotia|new\s+brunswick|newfoundland|prince\s+edward\s+island|"
    r"ottawa|gatineau|toronto|montreal|montréal|vancouver|calgary|edmonton|winnipeg|"
    r"waterloo|kitchener|mississauga|brampton|halifax|victoria)\b"
    r"|(?:^|[,\s(])(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE)(?:$|[,\s)])",
    re.IGNORECASE,
)
_FOREIGN_GEO_RE = re.compile(
    r"\b(?:united\s+states|u\.s\.a?\.?|usa|san\s+francisco|new\s+york|nyc|california|texas|"
    r"seattle|chicago|boston|austin|denver|atlanta|miami|los\s+angeles|united\s+kingdom|"
    r"great\s+britain|england|london,\s*uk|ireland|dublin|germany|berlin|france|paris|"
    r"singapore|india|bangalore|bengaluru|australia|sydney|melbourne|mexico|brazil)\b"
    r"|(?:^|[,\s(-])U\.?S\.?(?:$|[,\s)-])",
    re.IGNORECASE,
)

# Role families clearly outside Mohamed's financial operations, fraud/AML, collections,
# disputes, risk/compliance, and bilingual customer/banking support background.
_NON_TARGET_ROLE_PATTERNS = (
    re.compile(
        r"\b(?:software|full[- ]?stack|front[- ]?end|back[- ]?end|web|mobile|ios|android|cloud|"
        r"devops|sre|platform|infrastructure|systems?|network|embedded|firmware|cybersecurity|"
        r"application|qa|quality\s+assurance|test|sdet|ai|llm)\s+"
        r"(?:engineer(?:ing)?|developer|architect|programmer|scientist)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bfull[- ]?stack\s+development\b|\bsite\s+reliability\b", re.IGNORECASE),
    re.compile(
        r"\b(?:staff|principal|distinguished|fellow)\s+(?:software\s+)?(?:engineer|developer|architect|designer)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:product|technical\s+product|product\s+growth)\s+(?:manager|management|owner|lead|director|specialist)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:product|ux|ui|graphic|visual|brand|motion|web)\s+(?:designer|design|researcher)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:marketing|public\s+relations|copywriter|content\s+writer|seo|demand\s+gen|"
        r"brand|growth\s+marketing|product\s+marketing|lifecycle\s+marketing)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:account\s+executive|business\s+development|sales\s+development|sales\s+engineer|"
        r"sales\s+representative|shopping\s+centre|solutions\s+architect|solutions\s+engineer|"
        r"revenue\s+enablement|sales\s+enablement|operations\s+enablement|bdr|sdr)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:recruiter|talent\s+acquisition|people\s+partner|people\s+operations|hris|human\s+resources|"
        r"general\s+counsel|legal\s+counsel|paralegal|attorney)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:strategic\s+finance|fp&a|financial\s+planning|corporate\s+accounting|accounting\s+manager|"
        r"gl\s+operations|intercompany|tax\s+(?:manager|analyst|accountant)|payroll\s+(?:manager|specialist|administrator)|"
        r"it\s+operations|security\s+risk|financial\s+model\s+risk|"
        r"product\s+support\s+manager|manager,?\s+product\s+support)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:chief\s+\w+\s+officer|ceo|cto|cfo|coo|cpo|cro|cmo|ciso|svp|evp|vice\s+president)\b",
        re.IGNORECASE,
    ),
)

_TITLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "fraud": (
        "fincrime", "financial crime", "fraud", "scam", "trust & safety", "trust and safety",
        "investigation", "investigator", "claims", "disputes", "dispute", "chargeback",
        "risk", "aml", "kyc", "anti-money laundering", "sanctions", "transaction monitoring",
        "loss prevention", "identity theft",
    ),
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
    re.compile(r"\bcpa\s+(?:designation\s+)?(?:is\s+)?required\b", re.IGNORECASE),
    re.compile(r"\b(?:must|should)\s+(?:hold|have|possess)\s+(?:an?\s+)?(?:active\s+)?cpa\b", re.IGNORECASE),
    re.compile(r"\bactive\s+cpa\b", re.IGNORECASE),
    re.compile(r"\bholding\s+(?:a\s+)?cpa\b", re.IGNORECASE),
    re.compile(r"\bdesignated\s+cpa\b", re.IGNORECASE),
    re.compile(r"\bcpa\s+(?:or\s+equivalent)\s+required\b", re.IGNORECASE),
    re.compile(r"\bcpa\s+designation\s+\(or\s+equivalent\)\b", re.IGNORECASE),
)


# Role families that are toggled by TargetingPolicy (allowed but ranked lower in "broad",
# rejected in "core"). Clearly unrelated families stay in _NON_TARGET_ROLE_PATTERNS.
_SENIOR_LEADERSHIP_RE = re.compile(
    r"\b(?:senior|sr\.?)\s+manager\b|\bhead\s+of\b|\b(?:associate\s+|managing\s+|senior\s+)?director\b"
    r"|\bdirecteur\b|\bdirectrice\b",
    re.IGNORECASE,
)
_PEOPLE_MANAGER_RE = re.compile(r"\b(?:manager|supervisor|team\s+lead|gestionnaire)\b", re.IGNORECASE)
_DATA_SCIENCE_RE = re.compile(
    r"\b(?:data\s+scientist|data\s+science|machine\s+learning|ml\s+engineer|data\s+engineer|"
    r"research\s+scientist|applied\s+scientist|quantitative\s+(?:analyst|researcher|developer))\b",
    re.IGNORECASE,
)
_SALES_ACCOUNT_RE = re.compile(
    r"\b(?:account\s+manager|account\s+director|key\s+account|strategic\s+account|client\s+partner|"
    r"customer\s+success\s+manager|sales\s+manager|territory\s+manager)\b",
    re.IGNORECASE,
)
_ADJACENT_INVESTIGATION_RE = re.compile(
    r"\b(?:insurance|claims?\s+(?:investigator|adjuster|examiner)|private\s+investigator|surveillance|"
    r"loss\s+adjuster|special\s+investigations?\s+unit|siu)\b",
    re.IGNORECASE,
)
# Titles containing these stay "core" even if they also match an adjacent pattern
# (e.g. "AML Transaction Surveillance Analyst").
_CORE_DOMAIN_RE = re.compile(
    r"\b(?:fraud|aml|kyc|anti[- ]money|financial\s+crime|fincrime|sanctions|disputes?|chargebacks?|"
    r"collections?|credit|loan|lending|bank(?:ing)?|card(?:s)?|payments?|transaction)\b",
    re.IGNORECASE,
)


def _role_families(title: str) -> tuple[str, ...]:
    families: list[str] = []
    if _SENIOR_LEADERSHIP_RE.search(title):
        families.append("senior_leadership")
    if _DATA_SCIENCE_RE.search(title):
        families.append("data_science")
    if _SALES_ACCOUNT_RE.search(title):
        families.append("sales_account_mgmt")
    if _ADJACENT_INVESTIGATION_RE.search(title) and not (
        _CORE_DOMAIN_RE.search(title) and not re.search(r"\binsurance\b", title, re.IGNORECASE)
    ):
        families.append("adjacent_investigations")
    return tuple(families)


_FAMILY_TOGGLES = {
    "senior_leadership": "allow_senior_leadership",
    "data_science": "allow_data_science",
    "sales_account_mgmt": "allow_sales_account_mgmt",
    "adjacent_investigations": "allow_adjacent_investigations",
}
# In "broad", non-core families stay eligible but their role relevance is capped so
# core fraud/banking individual-contributor roles rank above them.
_NON_CORE_ROLE_CAP = 3.6


def _family_veto(title: str, policy: TargetingPolicy) -> DecisionReport | None:
    for family in _role_families(title):
        if not getattr(policy, _FAMILY_TOGGLES[family]):
            return DecisionReport(
                Decision.REJECT,
                f"role family '{family}' is disabled by targeting preset '{policy.preset}': {title}",
                0.0,
                vetoes=(f"role_family_{family}",),
            )
    return None


def _title_alignment_matches(title: str, target_keywords: tuple[str, ...]) -> tuple[str, ...]:
    """Match job title against target keywords and strictly related domain synonyms."""
    direct = _keyword_matches(title, target_keywords)
    if direct:
        return direct

    # Synonyms always expand, including for a single target keyword such as "fraud".
    norm_title = _normalize(title)
    expanded: list[str] = []
    seen: set[str] = set()
    for kw in target_keywords:
        for syn in _TITLE_SYNONYMS.get(_normalize(kw), ()):
            if syn not in seen and _term_in(norm_title, syn):
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
    family_veto = _family_veto(job.title or "", context.policy)
    if family_veto is not None:
        return family_veto, False

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
    location_canada = bool(_CANADA_GEO_RE.search(location))
    location_foreign = bool(_FOREIGN_GEO_RE.search(location))
    explicit_canada = bool(location_canada or ("canada" in norm_title and not location_foreign))
    excluded_location = _contains_any(location, context.excluded_locations)
    is_remote = (
        job.remote
        or any(term in norm_location or term in norm_title for term in _REMOTE_TERMS)
        or _has_affirmative_remote_desc(desc_sample)
    )
    target_location = _contains_any(location, context.target_locations) or (explicit_canada and is_remote)
    if excluded_location and not target_location:
        return DecisionReport(Decision.REJECT, "excluded location", 0.0, vetoes=("excluded_location",)), target_location

    explicit_foreign = location_foreign and not location_canada
    if explicit_foreign:
        return DecisionReport(
            Decision.REJECT,
            f"non-Canadian location: {location}",
            0.0,
            vetoes=("international_non_canada",),
        ), False

    is_ottawa_ncr = any(term in norm_location for term in _OTTAWA_NCR_TERMS)
    location_hybrid = bool(_HYBRID_ON_SITE_RE.search(location) or _HYBRID_ON_SITE_RE.search(job.title or ""))
    desc_hybrid = bool(_HYBRID_ON_SITE_RE.search(desc_sample[:300])) and not is_remote
    is_explicit_hybrid_or_onsite = location_hybrid or desc_hybrid

    # Check if a non-Ottawa location was explicitly targeted as a specific city (e.g. "Montréal")
    explicit_city_target = any(
        _word_in(norm_location, normalized_target)
        for target in context.target_locations
        if (normalized_target := _normalize(target))
        and normalized_target not in ("canada", "remote canada", "canada remote", "remote", "anywhere")
    )

    # Candidate rule: In-person or hybrid work is allowed ONLY in Ottawa-Gatineau (or explicitly targeted city),
    # unless the targeting policy allows in-person work anywhere in Canada ("broad").
    canada_targeted = any(_normalize(item) == "canada" for item in context.target_locations)
    onsite_elsewhere = (not is_remote or is_explicit_hybrid_or_onsite) and not is_ottawa_ncr and not explicit_city_target
    onsite_canada_allowed = bool(
        onsite_elsewhere and location and context.policy.allow_onsite_canada and explicit_canada and canada_targeted
    )
    if onsite_elsewhere and location and not (context.policy.allow_onsite_canada and explicit_canada):
        return DecisionReport(
            Decision.REJECT,
            f"non-remote or hybrid position outside Ottawa-Gatineau: {location}",
            0.0,
            vetoes=("non_remote_outside_ottawa",),
        ), False

    remote_targeted = is_remote and (explicit_canada or norm_location in ("", "remote", "anywhere")) and any(
        "remote" in _normalize(item) for item in context.target_locations if _normalize(item)
    )
    if not target_location and not remote_targeted and not onsite_canada_allowed:
        return DecisionReport(Decision.REJECT, "location not eligible", 0.0, vetoes=("location_not_eligible",)), target_location
    return None, target_location


_SECTOR_TERMS = ("bank", "banking", "financial", "fintech", "payments", "credit", "fraud", "aml", "kyc", "compliance")
_LANGUAGE_TERMS = ("bilingual", "french", "français", "english and french", "fr/en", "en/fr")
_IC_TITLE_TERMS = ("analyst", "officer", "specialist", "associate", "coordinator", "representative", "advisor", "investigator")
_SENIOR_EXEC_RE = re.compile(r"\b(?:vice\s+president|vp|principal)\b", re.IGNORECASE)


def _seniority(title: str, policy: TargetingPolicy) -> tuple[float, list[str]]:
    if _SENIOR_EXEC_RE.search(title):
        return 1.5, ["seniority may exceed target level"]
    if _SENIOR_LEADERSHIP_RE.search(title):
        # Only reachable when the policy allows senior leadership (otherwise vetoed earlier).
        return 1.5, [] if policy.allow_senior_leadership else ["seniority may exceed target level"]
    if (_PEOPLE_MANAGER_RE.search(title) or _contains_any(title, ("manager", "lead"))) and not _contains_any(
        title, _IC_TITLE_TERMS
    ):
        if _SALES_ACCOUNT_RE.search(title):
            return 2.5, []
        if not policy.allow_senior_leadership and _PEOPLE_MANAGER_RE.search(title):
            return 2.5, ["manager title may be a people-management role"]
        return 2.5, []
    return 4.5, []


def _dimensions(
    job: JobFacts,
    context: DecisionContext,
    matched_keywords: tuple[str, ...],
    title_keywords: tuple[str, ...],
    target_location: bool,
    families: tuple[str, ...] = (),
) -> tuple[tuple[DimensionScore, ...], list[str]]:
    """Score the six dimensions. Returns (dimensions, review flags)."""
    description = job.description or ""
    combined = f"{job.title or ''}\n{description}\n{job.company or ''}\n{job.location or ''}"

    title_score = min(3.2, 2.0 + 0.6 * max(0, len(title_keywords) - 1)) if title_keywords else 0.0
    desc_ratio = len(matched_keywords) / max(1, min(5, len(context.target_keywords)))
    role_score = _bounded_score(title_score + desc_ratio * 1.8)
    if families:
        role_score = min(role_score, _NON_CORE_ROLE_CAP)

    sector_hits = _keyword_matches(combined, _SECTOR_TERMS)
    language_hits = _keyword_matches(combined, _LANGUAGE_TERMS)
    seniority_score, seniority_flags = _seniority(job.title or "", context.policy)
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
    families = _role_families(job.title or "")
    if not title_keywords and "sales_account_mgmt" in families:
        # An enabled account-management title (e.g. "Strategic Account Manager") is aligned when the
        # posting itself is about the target domain; unrelated titles still need title alignment.
        title_keywords = _title_alignment_matches(job.description or "", context.target_keywords)
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

    dimensions, review_flags = _dimensions(job, context, matched_keywords, title_keywords, target_location, families)
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
