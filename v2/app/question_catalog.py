"""Application Question Catalog, Control Primitives, and Matching Engine.

Hardened question taxonomy, text normalization, and 4-tier option scoring
borrowed and adapted from JobTomatik for HunterXJob v2.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Multilingual truthy and falsy tokens (English & French)
_YES = {
    "yes", "y", "true", "1", "agree", "i agree", "accepted", "accept",
    "authorized", "eligible", "willing", "available", "oui", "vrai", "j accepte",
    "i consent", "consent", "i understand and agree",
}
_NO = {
    "no", "n", "false", "0", "decline", "declined", "do not agree",
    "not authorized", "not eligible", "not willing", "non", "faux", "je refuse",
    "i do not consent", "do not consent",
}
_PLACEHOLDERS = {
    "", "select", "select one", "choose", "choose one", "please select",
    "make a selection", "--", "none selected", "select...",
}


def normalize_text(value: Any) -> str:
    """Normalize text by stripping diacritics/accents, lowercasing, and normalizing punctuation."""
    raw = str(value or "").replace("\u2026", " ").replace("...", " ")
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", text)
    text = re.sub(r"[^a-z0-9+#./' -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def boolean_value(value: Any) -> bool | None:
    """Determine boolean truthiness with English and French support."""
    normalized = normalize_text(value)
    if normalized in _YES:
        return True
    if normalized in _NO:
        return False
    if normalized.startswith(("yes ", "oui ", "true ")):
        return True
    if normalized.startswith(("no ", "non ", "false ")):
        return False
    return None


@dataclass(frozen=True)
class OptionRecord:
    key: str
    label: str
    value: str
    disabled: bool = False
    selected: bool = False

    @property
    def normalized_label(self) -> str:
        return normalize_text(self.label)

    @property
    def normalized_value(self) -> str:
        return normalize_text(self.value)


@dataclass
class MatchResult:
    matched: list[OptionRecord] = field(default_factory=list)
    missing_answers: list[str] = field(default_factory=list)
    ambiguous_answers: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.matched) and not self.missing_answers and not self.ambiguous_answers

    @property
    def first_matched(self) -> OptionRecord | None:
        return self.matched[0] if self.matched else None


_SYNONYM_STEMS = {
    "bilingual": "biling",
    "bilingue": "biling",
    "professional": "profess",
    "professionnel": "profess",
    "professionnelle": "profess",
    "authorized": "authoriz",
    "autorise": "authoriz",
    "autorisee": "authoriz",
    "permanent": "perman",
    "immediate": "immediat",
    "relocate": "relocat",
    "relocation": "relocat",
}


def score_option(answer: str, option: OptionRecord) -> int:
    """Compute 4-tier match score between a candidate answer and an option record:
    100: Exact normalized text or value match
    92: Boolean equivalence (e.g. Yes/Oui/Agree, No/Non/Decline)
    84: Token Jaccard similarity >= 0.80 or cross-lingual synonym stem overlap
    70: Substring containment (minimum length >= 3)
    0: No match or disabled
    """
    normalized_answer = normalize_text(answer)
    label = option.normalized_label
    value = option.normalized_value
    if not normalized_answer or option.disabled:
        return 0

    # 1. Exact match
    if normalized_answer == label or normalized_answer == value:
        return 100

    # 2. Boolean equivalence
    answer_bool = boolean_value(normalized_answer)
    if answer_bool is not None and (
        answer_bool == boolean_value(label) or answer_bool == boolean_value(value)
    ):
        return 92

    # 3. Token similarity & cross-lingual synonym stems
    answer_tokens = set(normalized_answer.split())
    label_tokens = set(label.split())
    if answer_tokens and label_tokens:
        union = answer_tokens | label_tokens
        intersection = answer_tokens & label_tokens
        if union and (len(intersection) / len(union) >= 0.65):
            return 84
        stem_ans = {_SYNONYM_STEMS.get(w, w) for w in answer_tokens if len(w) > 2}
        stem_lbl = {_SYNONYM_STEMS.get(w, w) for w in label_tokens if len(w) > 2}
        stem_inter = stem_ans & stem_lbl
        if len(stem_inter) >= 2 or (len(stem_inter) >= 1 and any(s in _SYNONYM_STEMS.values() for s in stem_inter)):
            return 84

    # 4. Substring containment
    if len(normalized_answer) >= 2 and (
        normalized_answer in label or label in normalized_answer
        or normalized_answer in value or value in normalized_answer
    ):
        return 70

    return 0


def match_answers_to_options(
    answers: Sequence[str],
    options: Sequence[OptionRecord],
    *,
    allow_multiple: bool = False,
) -> MatchResult:
    """Match one or more target answers against a list of candidate options."""
    result = MatchResult()
    usable = [
        option for option in options
        if not option.disabled
        and option.normalized_label not in _PLACEHOLDERS
        and option.normalized_value not in _PLACEHOLDERS
    ]
    if not allow_multiple and len(answers) != 1:
        result.missing_answers.extend(answers or ["exactly one answer required"])
        return result

    used: set[str] = set()
    for answer in answers:
        scored: list[tuple[int, OptionRecord]] = [
            (score_option(answer, option), option)
            for option in usable
            if option.key not in used
        ]
        top = max((score for score, _ in scored), default=0)
        if top <= 0:
            result.missing_answers.append(answer)
            continue
        candidates = [option for score, option in scored if score == top]
        unique = {(item.normalized_label, item.normalized_value) for item in candidates}
        if len(unique) > 1:
            result.ambiguous_answers[answer] = [item.label or item.value for item in candidates]
            continue
        selected = candidates[0]
        result.matched.append(selected)
        used.add(selected.key)

    return result


def _item(
    canonical_key: str,
    label: str,
    category: str,
    sensitivity: str,
    description: str,
    patterns: list[str],
    *,
    setup_group: str,
    suggested_answers: list[str] | None = None,
    fallback_suggestions: list[str] | None = None,
    default_mode: str = "answer",
) -> dict[str, Any]:
    return {
        "canonical_key": canonical_key,
        "label": label,
        "category": category,
        "sensitivity": sensitivity,
        "description": description,
        "patterns": patterns,
        "setup_group": setup_group,
        "suggested_answers": suggested_answers or [],
        "fallback_suggestions": fallback_suggestions or [],
        "default_mode": default_mode,
    }


# Comprehensive Master Question Catalog
QUESTION_CATALOG: list[dict[str, Any]] = [
    # 1. Eligibility & Sponsorship
    _item(
        "sponsorship_required", "Requires employer sponsorship", "sponsorship", "legal",
        "Whether employer sponsorship is required now, or in a combined now-or-future question.",
        [
            r"will you now or in the future require.{0,40}(?:employer )?sponsorship",
            r"do you require (?:employer )?sponsorship",
            r"require visa sponsorship",
            r"need (?:employer )?sponsorship",
            r"immigration sponsorship required",
            r"require sponsorship to work",
            r"parrainage",
        ],
        setup_group="Eligibility", suggested_answers=["No", "Yes"],
        fallback_suggestions=["I do not require sponsorship", "No sponsorship required"],
    ),
    _item(
        "sponsorship_future_required", "Future sponsorship requirement", "sponsorship", "legal",
        "Whether you will require visa or immigration sponsorship in the future.",
        [
            r"will you (?:now or )?in the future require.{0,40}sponsorship",
            r"future.{0,50}sponsorship",
            r"later require.{0,40}sponsorship",
        ],
        setup_group="Eligibility", suggested_answers=["No", "Yes"],
        fallback_suggestions=["I will not require future sponsorship"],
    ),
    _item(
        "work_authorization", "Legally authorized to work", "work_authorization", "legal",
        "Whether you are legally authorized to work in the relevant country/jurisdiction.",
        [
            r"authorized to work",
            r"legally authorized",
            r"eligible to work",
            r"right to work",
            r"work authorization",
            r"autorisé[e]? à travailler",
            r"legally entitled to work",
        ],
        setup_group="Eligibility", suggested_answers=["Yes", "Authorized to work in Canada", "No"],
        fallback_suggestions=["Authorized to work in Canada", "Authorized to work in the US", "Yes"],
    ),
    _item(
        "citizenship_residency", "Citizenship or residency status", "work_authorization", "legal",
        "Citizenship, permanent-residency, or immigration status in Canada or United States.",
        [
            r"citizenship status",
            r"country of citizenship",
            r"what is your citizenship",
            r"are you a (?:canadian|u\.s\.|us) citizen",
            r"permanent resident status",
            r"are you a permanent resident",
            r"hold permanent residency",
            r"résident permanent",
            r"statut de citoyenneté",
        ],
        setup_group="Eligibility",
        suggested_answers=["Canadian citizen", "Permanent resident", "Canada", "Prefer not to answer"],
        fallback_suggestions=["Citizen", "Canada", "Canadian citizen"],
    ),
    _item(
        "age_requirement", "Meets legal age requirement", "legal", "legal",
        "Confirmation that you meet a stated legal minimum age requirement (18+).",
        [
            r"at least 18",
            r"minimum age",
            r"legal age",
            r"18 years of age",
            r"age of majority",
            r"âge légal",
        ],
        setup_group="Eligibility", suggested_answers=["Yes", "No"],
        fallback_suggestions=["I am 18 or older", "Yes"],
    ),
    _item(
        "security_clearance", "Security clearance", "screening", "legal",
        "Clearance level held or eligibility to obtain required clearance.",
        [
            r"current security clearance",
            r"security clearance",
            r"eligible for clearance",
            r"hold (?:a )?(?:reliability|secret|top secret) clearance",
            r"clearance level",
            r"cote de sécurité",
            r"fiabilité",
        ],
        setup_group="Eligibility",
        suggested_answers=["None", "Reliability Status", "Secret", "Eligible to obtain"],
        fallback_suggestions=["Eligible to obtain", "None", "Willing and eligible to obtain clearance"],
    ),
    _item(
        "export_control_eligibility", "Export-control eligibility", "screening", "legal",
        "Eligibility under export-control, ITAR, or EAR regulations.",
        [r"export control", r"itar", r"ear controlled", r"controlled technology", r"export compliance"],
        setup_group="Eligibility", suggested_answers=["Yes", "Eligible", "No"],
    ),

    # 2. Consents & Screenings
    _item(
        "background_check_consent", "Background-check consent", "screening", "legal",
        "Consent to a standard pre-employment background or reference check.",
        [
            r"background check",
            r"background screening",
            r"pre-employment background",
            r"consent to.*background",
            r"undergo.*background",
            r"vérification des antécédents",
        ],
        setup_group="Eligibility", suggested_answers=["Yes", "No"],
        fallback_suggestions=["I consent", "Yes"],
    ),
    _item(
        "drug_screening_consent", "Drug-screening consent", "screening", "legal",
        "Consent to pre-employment drug or substance screening when required.",
        [r"drug test", r"drug screening", r"substance screening", r"pre-employment drug"],
        setup_group="Eligibility", suggested_answers=["Yes", "No"],
    ),
    _item(
        "credit_check_consent", "Credit-check consent", "screening", "legal",
        "Consent to an employment-related credit history check.",
        [r"credit check", r"credit screening", r"credit history check"],
        setup_group="Eligibility", suggested_answers=["Yes", "No"],
    ),
    _item(
        "driving_record_check_consent", "Driving-record check consent", "screening", "legal",
        "Consent to a motor-vehicle or driving-record (abstract) check.",
        [r"driving record", r"motor vehicle record", r"mvr check", r"driver abstract"],
        setup_group="Eligibility", suggested_answers=["Yes", "No"],
    ),
    _item(
        "employment_verification_consent", "Employment-verification consent", "consent", "legal",
        "Permission to verify prior employment history.",
        [r"verify (?:my |your )?(?:prior )?employment", r"employment verification", r"verify employment history"],
        setup_group="Consent", suggested_answers=["Yes", "No", "Ask me first"],
    ),
    _item(
        "education_verification_consent", "Education-verification consent", "consent", "legal",
        "Permission to verify educational degrees, diplomas, or certificates.",
        [r"verify (?:my |your )?education", r"education verification", r"verify (?:my |your )?(?:degree|diploma|credentials)"],
        setup_group="Consent", suggested_answers=["Yes", "No", "Ask me first"],
    ),
    _item(
        "terms_consent", "Application declaration or terms", "consent", "legal",
        "Agreement to application terms, declarations, accuracy certification, or privacy policy.",
        [
            r"read and agree",
            r"i certify",
            r"i acknowledge",
            r"terms and conditions",
            r"(?:i )?agree to (?:the )?(?:application )?terms",
            r"disclaimer and consent",
            r"privacy notice",
            r"privacy policy",
            r"data processing",
            r"information.*(?:true|accurate|complete)",
            r"j'accepte",
            r"je certifie",
        ],
        setup_group="Consent", suggested_answers=["Yes", "I agree", "I certify"],
    ),
    _item(
        "non_compete", "Non-compete or restrictive covenants", "legal", "legal",
        "Whether you are bound by any non-compete, non-solicitation, or restrictive covenants.",
        [r"non-compete", r"restrictive covenant", r"conflict of interest", r"non-solicitation"],
        setup_group="Eligibility", suggested_answers=["No", "Yes"],
    ),
    _item(
        "essential_functions_ability", "Able to perform essential job functions", "screening", "legal",
        "Whether you are able to perform the role's essential functions, with or without accommodation.",
        [r"perform (?:the )?essential functions", r"essential duties.{0,50}(?:with or without )?accommodation", r"able to perform.{0,60}job functions"],
        setup_group="Eligibility", suggested_answers=["Yes", "No", "With accommodation"],
    ),

    # 3. Compensation
    _item(
        "salary_expectation", "Base salary expectation", "compensation", "sensitive",
        "Desired annual base salary or salary range.",
        [
            r"salary expectation",
            r"expected salary",
            r"desired salary",
            r"base salary expectation",
            r"desired base salary",
            r"annual salary",
            r"salary range are you seeking",
            r"prétentions salariales",
        ],
        setup_group="Compensation", suggested_answers=["$75,000", "75000", "75,000 - 85,000"],
    ),
    _item(
        "hourly_rate_expectation", "Hourly-rate expectation", "compensation", "sensitive",
        "Desired hourly wage or contract rate.",
        [r"hourly rate expectation", r"expected hourly rate", r"desired hourly rate", r"hourly pay expectation", r"rate per hour"],
        setup_group="Compensation", suggested_answers=["$38/hr", "38", "$36 - $42"],
    ),
    _item(
        "compensation_negotiable", "Compensation negotiability", "compensation", "sensitive",
        "Whether your compensation expectation is flexible or negotiable.",
        [r"salary.{0,40}negotiable", r"compensation.{0,40}negotiable", r"flexible on.{0,30}(?:salary|compensation|pay)", r"open to negotiating"],
        setup_group="Compensation", suggested_answers=["Yes", "Negotiable", "Depends on total package"],
    ),

    # 4. Work Preferences & Schedule
    _item(
        "commute_ability", "Able to commute to work location", "location", "standard",
        "Whether you can reliably commute to the stated work location or office.",
        [r"able to commute", r"reliably commute", r"commute to.{0,80}(?:office|location|site)", r"reasonable commuting distance"],
        setup_group="Work preferences", suggested_answers=["Yes", "No"],
    ),
    _item(
        "relocation", "Willing to relocate", "relocation", "standard",
        "Willingness to relocate for the position.",
        [r"willing to relocate", r"relocate for this role", r"open to relocation", r"relocation"],
        setup_group="Work preferences", suggested_answers=["Yes", "No"],
    ),
    _item(
        "relocation_assistance", "Relocation assistance needed", "relocation", "sensitive",
        "Whether accepting the role would require financial relocation assistance.",
        [r"require relocation assistance", r"need relocation assistance", r"relocation support", r"relocation package"],
        setup_group="Work preferences", suggested_answers=["No", "Yes", "Open to discuss"],
    ),
    _item(
        "remote_workspace", "Suitable remote workspace and equipment", "work_arrangement", "standard",
        "Whether you have a dedicated, quiet workspace and high-speed internet for remote work.",
        [
            r"dedicated workspace",
            r"suitable home office",
            r"private workspace",
            r"remote work environment",
            r"quiet workspace",
            r"reliable internet",
            r"high-speed internet",
            r"remote work equipment",
        ],
        setup_group="Work preferences", suggested_answers=["Yes", "No"],
    ),
    _item(
        "schedule_hours", "Hours and schedule availability", "availability", "standard",
        "Availability for full-time hours, business hours, or shifts.",
        [
            r"available for full\s*time",
            r"full-time basis",
            r"comfortable working a schedule",
            r"aligned with these business hours",
            r"hours per week",
            r"40 hours per week",
        ],
        setup_group="Work preferences", suggested_answers=["Yes", "40", "Full-time"],
    ),
    _item(
        "on_call_availability", "On-call availability", "availability", "standard",
        "Whether you can participate in an on-call, escalation, or standby rotation.",
        [r"on-call", r"on call rotation", r"standby rotation", r"after-hours rotation"],
        setup_group="Work preferences", suggested_answers=["Yes", "No", "Occasionally"],
    ),
    _item(
        "notice_period", "Notice period or start date", "availability", "standard",
        "Earliest start date or required notice period.",
        [r"start date", r"earliest date", r"notice period", r"when can you start", r"earliest start date", r"availability to start"],
        setup_group="Work preferences", suggested_answers=["2 weeks", "Immediately", "2-3 weeks"],
    ),
    _item(
        "how_heard", "How did you hear about this opportunity", "source", "standard",
        "The source or referral channel where you found the job listing.",
        [r"how did you hear", r"where did you find", r"source of application", r"how did you find out about us"],
        setup_group="Source", suggested_answers=["LinkedIn", "Company Website", "Indeed"],
    ),

    # 5. Experience, Qualifications & Management
    _item(
        "financial_experience", "Banking and financial services experience", "qualification", "standard",
        "Experience in financial institutions, banking, credit, collections, operations, or fraud.",
        [
            r"experience in (?:banking|financial services|fintech|financial operations)",
            r"experience with (?:fraud|risk|collections|credit|kyc|kyb|aml)",
            r"worked in (?:financial|banking|credit|risk)",
        ],
        setup_group="Qualifications", suggested_answers=["Yes", "5+ years", "Yes - 5+ years"],
    ),
    _item(
        "management_experience", "People-management or team leadership experience", "qualification", "standard",
        "Direct people-management, supervisory, or team-lead experience.",
        [r"people management experience", r"management experience", r"supervisory experience", r"managed (?:a )?team", r"led (?:a )?team"],
        setup_group="Qualifications", suggested_answers=["Yes", "No"],
    ),
    _item(
        "management_years", "Years of management experience", "qualification", "standard",
        "Years of direct people-management or supervisory experience.",
        [r"how many years.{0,30}(?:management|supervisory|people management)", r"years of (?:management|supervisory|people management) experience"],
        setup_group="Qualifications", suggested_answers=["3", "3 years", "3-5 years"],
    ),
    _item(
        "team_size_managed", "Largest team size managed", "qualification", "standard",
        "Number of direct reports or largest team size managed.",
        [r"how many (?:direct reports|people).{0,40}(?:manage|managed|supervise)", r"largest team.{0,30}(?:managed|led)", r"team size.{0,30}(?:managed|supervised)"],
        setup_group="Qualifications", suggested_answers=["5", "3-5", "1-5"],
    ),
    _item(
        "language_french", "French language proficiency", "qualification", "standard",
        "Proficiency in French speaking, reading, and writing.",
        [r"french", r"français", r"fluency in french", r"proficient in french", r"bilingual in french"],
        setup_group="Qualifications", suggested_answers=["Fluent", "Professional working proficiency", "Yes"],
    ),
    _item(
        "language_english", "English language proficiency", "qualification", "standard",
        "Proficiency in English speaking, reading, and writing.",
        [r"english", r"anglais", r"fluency in english", r"proficient in english"],
        setup_group="Qualifications", suggested_answers=["Fluent", "Professional working proficiency", "Native", "Yes"],
    ),

    # 6. Employment History & Prior Contact
    _item(
        "prior_employment", "Previously employed by company", "employment", "standard",
        "Whether you previously worked for or were employed by this company or affiliate.",
        [
            r"previously (?:work|employ)",
            r"ever (?:been )?(?:previously )?(?:work|employ)",
            r"former employee",
            r"worked here before",
        ],
        setup_group="Employment", suggested_answers=["No", "Yes"],
    ),
    _item(
        "prior_application", "Previously applied to employer", "employment", "standard",
        "Whether you previously applied for a role with this employer.",
        [r"previously applied (?:to|for|with)", r"applied (?:to|for).{0,50}before", r"ever applied (?:to|with)"],
        setup_group="Employment", suggested_answers=["No", "Yes"],
    ),
    _item(
        "prior_interview", "Previously interviewed by employer", "employment", "standard",
        "Whether you previously interviewed with this employer.",
        [r"previously interviewed", r"interviewed (?:with|at).{0,50}before", r"ever interviewed (?:with|at)"],
        setup_group="Employment", suggested_answers=["No", "Yes"],
    ),
    _item(
        "termination_history", "Termination or dismissal history", "employment", "sensitive",
        "Whether you have ever been terminated, dismissed, or asked to resign from employment.",
        [r"ever been terminated", r"ever been dismissed", r"asked to resign", r"fired from (?:a )?job"],
        setup_group="Employment", suggested_answers=["No", "Yes"],
    ),
    _item(
        "rehire_eligibility", "Eligible for rehire", "employment", "sensitive",
        "Whether former employers consider you eligible for rehire.",
        [r"eligible for rehire", r"rehire eligibility", r"eligible to be rehired"],
        setup_group="Employment", suggested_answers=["Yes", "No"],
    ),
    _item(
        "contact_current_employer", "Permission to contact current employer", "consent", "sensitive",
        "Whether the employer may contact your current employer.",
        [r"contact (?:my |your )?current employer", r"may we contact.{0,30}current employer", r"permission.{0,40}current employer"],
        setup_group="Employment", suggested_answers=["Yes", "Ask me first", "No"],
    ),
    _item(
        "contact_former_employers", "Permission to contact former employers", "consent", "sensitive",
        "Whether the employer may contact previous/former employers.",
        [r"contact (?:my |your )?(?:former|previous|prior) employers?", r"may we contact.{0,30}(?:former|previous|prior) employers?"],
        setup_group="Employment", suggested_answers=["Yes", "No", "Ask me first"],
    ),

    # 7. Voluntary Demographics & EEO (Defaulting safely to Decline / Prefer not to answer)
    _item(
        "gender_identity", "Gender identity", "demographic", "sensitive",
        "Voluntary gender identity self-identification.",
        [r"gender identity", r"what is your gender", r"how do you identify your gender", r"identité de genre", r"\bgender\b"],
        setup_group="Voluntary demographics",
        suggested_answers=["Male", "Prefer not to answer", "Decline to self-identify"],
        fallback_suggestions=["Man", "M", "Prefer not to say", "Decline to answer"],
    ),
    _item(
        "race_ethnicity", "Race and ethnicity", "demographic", "sensitive",
        "Voluntary race and ethnicity self-identification.",
        [r"race and ethnicity", r"race/ethnicity", r"racial background", r"ethnic background", r"racial or ethnic", r"what is your race", r"select your race", r"\brace\b"],
        setup_group="Voluntary demographics",
        default_mode="decline",
        suggested_answers=["Prefer not to answer", "Decline to self-identify", "Middle Eastern or North African"],
        fallback_suggestions=["I do not wish to answer", "Decline to answer", "Prefer not to disclose"],
    ),
    _item(
        "hispanic_latino", "Hispanic or Latino origin", "demographic", "sensitive",
        "Voluntary Hispanic, Latino, or Spanish origin disclosure.",
        [r"hispanic or latino", r"hispanic/latino", r"latino or hispanic", r"spanish origin"],
        setup_group="Voluntary demographics",
        default_mode="decline",
        suggested_answers=["No", "Prefer not to answer", "Decline to self-identify"],
    ),
    _item(
        "veteran_status", "Veteran status", "demographic", "sensitive",
        "Voluntary veteran or military service self-identification.",
        [r"veteran status", r"protected veteran", r"military service", r"served in the (?:military|armed forces)", r"ancien combattant"],
        setup_group="Voluntary demographics",
        suggested_answers=["I am not a protected veteran", "No", "Prefer not to answer", "Decline to self-identify"],
        fallback_suggestions=["Not a veteran", "I do not identify as a protected veteran"],
    ),
    _item(
        "disability_status", "Disability status", "demographic", "sensitive",
        "Voluntary disability self-identification.",
        [r"disability status", r"person with a disability", r"do you have a disability", r"disabled", r"handicap", r"personne en situation de handicap"],
        setup_group="Voluntary demographics",
        suggested_answers=["No, I do not have a disability", "No", "Prefer not to answer", "Decline to self-identify"],
        fallback_suggestions=["I do not have a disability", "I do not wish to answer"],
    ),

    # 8. Operational, Technical & Industry Screening
    _item(
        "evening_weekend_availability", "Evening and weekend availability", "availability", "standard",
        "Availability to work evening, weekend, or rotating shift schedules.",
        [
            r"available to work evenings and weekends",
            r"work evenings and weekends",
            r"evening and weekend",
            r"weekend shifts",
            r"disponible soirs et fins de semaine",
            r"rotating shifts?",
        ],
        setup_group="Work preferences",
        suggested_answers=["Yes", "Flexible", "No"],
        fallback_suggestions=["Yes, available for evenings and weekends", "Yes"],
    ),
    _item(
        "sql_experience", "SQL knowledge and experience rating", "qualification", "standard",
        "Experience level, scale rating, or professional background using SQL.",
        [
            r"experience and knowledge in sql",
            r"level of experience.{0,30}sql",
            r"scale of 1 to 5.{0,30}sql",
            r"using sql in a professional",
            r"sql proficiency",
        ],
        setup_group="Qualifications",
        suggested_answers=["4", "3", "Intermediate SQL experience (queries, joins, aggregations)"],
        fallback_suggestions=["4 - Proficient in SQL queries, joins, and analysis"],
    ),
    _item(
        "smb_credit_lending", "Small business / commercial lending experience", "qualification", "standard",
        "Experience in small business credit risk, underwriting, or commercial lending.",
        [
            r"underwriting or setting credit policy",
            r"credit risk, credit policy, or credit analytics",
            r"small business or commercial lending",
            r"smb lending",
            r"credit lifecycle",
        ],
        setup_group="Qualifications",
        suggested_answers=["Yes", "5+ years", "Yes, direct experience in credit analysis and delinquency management"],
    ),
    _item(
        "interview_recording_consent", "Interview recording consent", "consent", "sensitive",
        "Consent to have virtual or video interviews recorded for evaluation purposes.",
        [
            r"interview recording consent",
            r"consent to record (?:video )?interviews?",
            r"recorded during the interview",
            r"consent to being recorded",
        ],
        setup_group="Consent",
        suggested_answers=["Yes", "I consent", "I agree"],
    ),
    _item(
        "identity_verification_consent", "Identity verification consent", "consent", "legal",
        "Agreement to complete identity verification prior to interviews.",
        [
            r"identity verification",
            r"verify your identity prior",
            r"complete a secure identity verification",
            r"id verification",
        ],
        setup_group="Consent",
        suggested_answers=["Yes", "I agree", "Acknowledge"],
    ),
    _item(
        "pci_background_check_consent", "PCI compliance / criminal background check", "consent", "legal",
        "Agreement to complete criminal background check for PCI compliance.",
        [
            r"pci complian(?:t|ce)",
            r"clear a criminal check",
            r"criminal check so that .{0,30} can remain pci compliant",
            r"obtain positive results",
        ],
        setup_group="Consent",
        suggested_answers=["Yes", "I agree", "I acknowledge and agree"],
    ),
    _item(
        "located_in_canada", "Currently located / residing in Canada", "location", "standard",
        "Confirmation that you currently reside in or are located in Canada.",
        [
            r"located in canada",
            r"reside in canada",
            r"living in canada",
            r"currently based in canada",
            r"resident of canada",
            r"résidez-vous au canada",
        ],
        setup_group="Eligibility",
        suggested_answers=["Yes", "Canada", "Yes, located in Canada"],
        fallback_suggestions=["Yes", "Located in Canada"],
    ),
    _item(
        "salary_range_alignment", "Alignment with posted salary range", "compensation", "sensitive",
        "Confirmation that your expectations are aligned with the posted salary range.",
        [
            r"reviewed the posted salary range",
            r"expectations aligned with (?:it|the posted range)",
            r"comfortable with the posted (?:salary|compensation) range",
        ],
        setup_group="Compensation",
        suggested_answers=["Yes", "Aligned", "Yes, expectations aligned with range"],
    ),
    _item(
        "pronouns", "Preferred pronouns", "demographic", "standard",
        "Voluntary pronoun selection (e.g. He/Him, They/Them, She/Her).",
        [
            r"preferred pronouns",
            r"what are your pronouns",
            r"pronoun",
            r"pronoms",
        ],
        setup_group="Voluntary demographics",
        suggested_answers=["He/Him", "Prefer not to say", "He/Him/His"],
    ),
    _item(
        "why_company_motivation", "Motivation and why apply to company", "qualification", "standard",
        "Why you are applying or what motivates you in a work environment.",
        [
            r"why (?:do you want to work|are you interested in)",
            r"what influenced your decision to apply",
            r"what motivates you to do what you do",
            r"what do you value in a work environment",
            r"why this role",
        ],
        setup_group="Qualifications",
        suggested_answers=["Passionate about fintech, customer financial health, and operational excellence"],
    ),
    _item(
        "colleague_perspective", "Colleague or teammate feedback", "qualification", "standard",
        "What co-workers or colleagues would say about working with you.",
        [
            r"what would your co-workers say",
            r"best thing about having you as a colleague",
            r"how would colleagues describe you",
        ],
        setup_group="Qualifications",
        suggested_answers=["Collaborative, reliable, calm under pressure, and dedicated to supporting the team"],
    ),
    _item(
        "referral_person", "Employee referral name", "source", "standard",
        "Name of employee or connection who referred you to the position.",
        [
            r"if someone from .{0,30} referred you",
            r"list their name here",
            r"referred by an employee",
            r"referral name",
        ],
        setup_group="Source",
        suggested_answers=["N/A", "None", "Self-applied"],
    ),
]


def classify_question(question_text: str) -> dict[str, Any] | None:
    """Classify a question by the longest matching prompt fragment across all catalog definitions."""
    normalized = normalize_text(question_text)
    if not normalized:
        return None

    matches: list[tuple[int, int, dict[str, Any]]] = []
    for index, item in enumerate(QUESTION_CATALOG):
        for pattern in item["patterns"]:
            norm_pattern = normalize_text(pattern)
            match = re.search(pattern, normalized, flags=re.IGNORECASE) or re.search(norm_pattern, normalized, flags=re.IGNORECASE)
            if match:
                matches.append((len(match.group(0)), -index, item))

    if matches:
        _, _, best_item = max(matches, key=lambda value: (value[0], value[1]))
        return best_item
    return None
