"""Truthfulness guard for generated application materials.

Every claim in a résumé or cover letter must trace back to a *verified*
profile fact. The guard is used two ways:

* structurally, on the résumé document: every entry must reference a verified
  fact, and employer, title, dates, degree and institution must equal that
  fact exactly (code copies them; nothing may rewrite them);
* textually, on any free text (bullets, summary, cover letter), especially
  text reworded by an LLM: numbers, spelled-out quantities, dates, proper
  names, seniority/degree words and skill terms must all occur in the
  verified facts (bullets: in the bullet's own source fact).

Anything unexplained is a violation. Callers reject the text and fall back to
the deterministic template. The guard errs on the side of rejecting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.profile import Fact, VerifiedProfile

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9+#&.'\u2019-]*")
_SENTENCE_END_RE = re.compile(r"[.!?:;\n]\s*$")
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec",
})
SPELLED_NUMBERS = frozenset({
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "hundreds",
    "thousand", "thousands", "million", "millions", "billion", "billions", "dozen", "dozens",
    "double", "doubled", "doubling", "triple", "tripled", "tripling", "quadrupled", "half", "halved",
    "twice", "tenfold", "percent",
})
# Words that claim a rank, credential or qualification.
CREDENTIAL_WORDS = frozenset({
    "senior", "sr", "junior", "lead", "principal", "staff", "director", "manager", "head", "vp",
    "vice", "president", "chief", "cto", "ceo", "cfo", "coo", "founder", "co-founder", "architect",
    "supervisor", "executive", "partner", "owner", "bachelor", "bachelor's", "master", "master's",
    "phd", "ph.d", "doctorate", "doctoral", "mba", "degree", "diploma", "certified", "certification",
    "certificate", "licensed", "license", "accredited", "chartered", "award", "awarded", "patent",
    "patented", "published", "promoted", "fluent", "native", "bilingual", "trilingual",
})
# Lower-case skill/tool vocabulary. A term from this list (or from the job
# description) that appears in generated text must exist in the verified facts.
SKILL_TERMS = frozenset({
    "python", "java", "javascript", "typescript", "golang", "rust", "ruby", "php", "scala", "kotlin",
    "swift", "c++", "c#", "sql", "nosql", "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis",
    "kafka", "spark", "hadoop", "airflow", "dbt", "snowflake", "bigquery", "redshift", "databricks",
    "tableau", "looker", "excel", "vba", "sas", "spss", "stata", "matlab", "pandas", "numpy",
    "tensorflow", "pytorch", "scikit-learn", "keras", "llm", "nlp", "kubernetes", "docker", "terraform",
    "ansible", "jenkins", "git", "github", "gitlab", "linux", "aws", "azure", "gcp", "react", "angular",
    "vue", "node.js", "django", "flask", "fastapi", "graphql", "salesforce", "sap",
    "oracle", "jira", "confluence", "servicenow", "zendesk", "hubspot", "workday", "quickbooks",
    "actimize", "mantas", "featurespace", "sentinel", "splunk", "powerbi", "figma",
    "aml", "kyc", "cdd", "edd", "sanctions", "ofac", "fintrac", "pci", "pci-dss", "sox", "gdpr", "hipaa",
    "agile", "scrum", "kanban", "sigma", "itil", "pmp", "cams", "cfe", "cpa", "cfa",
    "chargebacks", "chargeback", "underwriting", "collections", "accounting", "auditing", "audit",
    "forensics", "statistics", "modeling", "modelling",
})
_COMMON_TEXT = """
a about above across after again against all also am among an and any are as at be because been before
being below between both but by can could did do does doing done down during each either else enough
especially even ever every few for from further had has have having he her here hers him his how however
i if in into is it its itself just least less like made make makes making many may me more most much
must my myself near need needs neither no nor not now of off often on once one only onto or other our
ours out over own per quite rather same several she should since so some still such than that the their
them then there these they this those though through thus to too toward under until up upon us very via
was we well were what when where whether which while who whom whose why will with within without would
yet you your yours dear sincerely regards thank thanks hello hi hiring team role position job opportunity
company organization apply applying application interest interested excited pleased look looking forward
consideration candidate experience experienced background skills skill strengths strong ability able
work worked working works career years year months month day days time today currently current recent
recently previously prior including include includes included across new key core relevant directly
closely daily weekly monthly quarterly annual annually team's teams across customers customer clients
client users user stakeholders partners business operations process processes support supported
supporting help helped helping deliver delivered delivering manage managed managing improve improved
improving reduce reduced reducing increase increased increasing build built building create created
creating design designed designing develop developed developing led lead leading own owned run ran
running drive drove driving grow grew growing cut set wrote write taught teach brought bring handled
handle handling resolved resolve resolving answered answer answering documented document documenting
automated automate automating analyzed analyse analysed analyze analyzing reviewed review reviewing
coordinated coordinate partnered collaborated collaborate worked trained train mentored mentor
oversaw oversee launched launch implemented implement maintained maintain monitored monitor
investigated investigate identified identify prepared prepare provided provide served serve ensured
ensure achieved achieve spearheaded streamlined streamline optimized optimised optimize enabled enable
redesigned redesign established establish introduced introduce produced produce performed perform
using used use through via throughout per cent rate rates result results resulting impact impacts
quality accuracy efficiency efficient effective effectively accurate accurately clear clearly
detailed details detail thorough reliable consistent consistently successfully success successful
alerts alert cases case reports report reporting data tools tool systems system rules rule teams
procedures procedure policies policy controls control issues issue inquiries inquiry escalations
escalation requests request tasks task projects project dashboards dashboard scripts script team
my i'm i've i'd me myself am your you're you've our we're what's it's that's there's
role's roles responsibilities responsibility opportunity opportunities mission values value
contribute contributing contribution contributions bring bringing offer offering would like
believe confident eager glad happy keen motivated passionate ready enjoy enjoyed
following requirements posting match hold considering welcome chance discuss
"""
COMMON_WORDS = frozenset(_COMMON_TEXT.split())


def _norm_number(token: str) -> str:
    value = token.replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".") or "0"
    return value.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    return {_norm_number(token) for token in _NUMBER_RE.findall(text or "")}


def _words(text: str) -> list[str]:
    return [word.strip(".'\u2019-").lower() for word in _WORD_RE.findall(text or "") if word.strip(".'\u2019-")]


def format_date(value: str | None) -> str:
    """'2021-03' -> 'Mar 2021'; '2021' -> '2021'; None -> ''."""
    if not value:
        return ""
    if len(value) == 7:
        return f"{_MONTH_NAMES[int(value[5:7]) - 1]} {value[:4]}"
    return value


def fact_strings(fact: Fact) -> list[str]:
    """All human-readable text of a fact, including its formatted dates."""
    out: list[str] = []
    for key, value in fact.data.items():
        if isinstance(value, str):
            out.append(value)
            if key in {"start", "end", "date", "expires"}:
                out.append(format_date(value))
        elif isinstance(value, list):
            out.extend(str(item) for item in value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(str(value))
    return out


@dataclass(slots=True)
class Corpus:
    """What verified facts allow generated text to say."""

    text: str
    words: set[str]
    numbers: set[str]
    allowed_names: set[str] = field(default_factory=set)
    forbidden_terms: set[str] = field(default_factory=set)


def corpus_for(facts: Iterable[Fact], *, allowed_names: Iterable[str] = (), job_text: str = "") -> Corpus:
    strings = [text for fact in facts for text in fact_strings(fact)]
    blob = "\n".join(strings)
    words = set(_words(blob))
    names = {word for name in allowed_names for word in _words(name)}
    # Job-description vocabulary that is not in the verified facts must not
    # leak into the candidate's claims (e.g. a tool the job asks for).
    job_terms = {word for word in _words(job_text) if len(word) >= 3 and word not in COMMON_WORDS}
    forbidden = (job_terms | SKILL_TERMS | CREDENTIAL_WORDS | SPELLED_NUMBERS | _MONTH_WORDS) - words - names
    return Corpus(text=blob.lower(), words=words, numbers=numbers_in(blob), allowed_names=names, forbidden_terms=forbidden)


def profile_corpus(profile: VerifiedProfile, *, allowed_names: Iterable[str] = (), job_text: str = "") -> Corpus:
    return corpus_for(profile.facts, allowed_names=allowed_names, job_text=job_text)


def _capitalized_tokens(text: str) -> list[tuple[str, bool]]:
    """(word, sentence_initial) for each capitalized word."""
    out = []
    for match in _WORD_RE.finditer(text or ""):
        word = match.group(0).strip(".'\u2019-")
        if not word or not word[0].isupper():
            continue
        before = text[:match.start()]
        initial = not before.strip() or bool(_SENTENCE_END_RE.search(before)) or before.rstrip().endswith(("•", "-", ","))
        out.append((word, initial))
    return out


def _name_violations(text: str, corpus: Corpus) -> list[str]:
    violations = []
    for word, initial in _capitalized_tokens(text):
        lower = word.lower()
        if lower in corpus.words or lower in corpus.allowed_names:
            continue
        if initial and (lower in COMMON_WORDS or (lower.endswith("ed") and lower not in corpus.forbidden_terms)):
            continue
        if lower in COMMON_WORDS and lower not in corpus.forbidden_terms:
            continue
        violations.append(f"name or term not in verified facts: {word!r}")
    return violations


def check_text(text: str, corpus: Corpus, *, source: str | None = None) -> list[str]:
    """Violations in ``text``. With ``source``, numbers must come from that exact fact text."""
    violations: list[str] = []
    allowed_numbers = numbers_in(source) if source is not None else corpus.numbers
    for number in sorted(numbers_in(text) - allowed_numbers):
        where = "its source fact" if source is not None else "the verified facts"
        violations.append(f"number {number!r} does not appear in {where}")
    source_words = set(_words(source)) if source is not None else corpus.words
    for word in sorted(set(_words(text))):
        if word in corpus.forbidden_terms or (source is not None and word in SPELLED_NUMBERS and word not in source_words):
            violations.append(f"unsupported claim word {word!r} (not in the verified facts)")
    violations.extend(_name_violations(text, corpus))
    return sorted(set(violations))


# ------------------------------------------------------------ résumé structure

_EXACT_FIELDS = {
    "experience": ("employment", ("employer", "title", "location", "start", "end")),
    "education": ("education", ("institution", "degree", "field_of_study", "start", "end")),
    "projects": ("project", ("name", "role", "start", "end")),
}


def _check_entry(entry: dict, facts: dict[str, Fact], category: str, fields: tuple[str, ...]) -> list[str]:
    fact = facts.get(entry.get("fact", ""))
    if fact is None or fact.category != category:
        return [f"{category} entry {entry.get('fact')!r} is not a verified {category} fact"]
    return [
        f"{category} {name} {entry.get(name)!r} differs from the verified fact"
        for name in fields if (entry.get(name) or None) != (fact.data.get(name) or None)
    ]


def _check_bullets(entry: dict, facts: dict[str, Fact], corpus: Corpus) -> list[str]:
    violations = []
    for bullet in entry.get("bullets", []):
        fact = facts.get(bullet.get("fact", ""))
        if fact is None or fact.category != "achievement":
            violations.append(f"bullet {bullet.get('text', '')[:40]!r} has no verified achievement")
            continue
        if entry.get("fact") not in (fact.data.get("employment"), fact.data.get("project")):
            violations.append(f"achievement {fact.key} is attached to the wrong entry")
        if bullet.get("text") != fact.data["text"]:
            source = "\n".join(fact_strings(fact))
            violations.extend(check_text(bullet.get("text", ""), corpus, source=source))
    return violations


def _check_items(items: list[dict], facts: dict[str, Fact], category: str) -> list[str]:
    return [f"{category} item {item.get('text', '')!r} is not a verified {category} fact"
            for item in items if getattr(facts.get(item.get("fact", "")), "category", None) != category]


def check_resume(doc: dict, profile: VerifiedProfile) -> list[str]:
    """Structural + textual check of a résumé document against verified facts."""
    facts = profile.by_key()
    corpus = profile_corpus(profile, job_text=str(doc.get("job", {}).get("description", "")))
    violations: list[str] = []
    for section, (category, fields) in _EXACT_FIELDS.items():
        for entry in doc.get(section, []):
            violations.extend(_check_entry(entry, facts, category, fields))
            violations.extend(_check_bullets(entry, facts, corpus))
    for section, category in (("skills", "skill"), ("certifications", "certification"),
                              ("languages", "language"), ("contact", "contact")):
        violations.extend(_check_items(doc.get(section, []), facts, category))
    summary = doc.get("summary")
    if summary:
        fact = facts.get(summary.get("fact", ""))
        if fact is None or fact.category != "summary":
            violations.append("summary is not a verified summary fact")
        elif summary.get("text") != fact.data["text"]:
            violations.extend(check_text(summary.get("text", ""), corpus, source=fact.data["text"]))
    return sorted(set(violations))


# ----------------------------------------------------------------- cover letter


def _strip_target(text: str, job_title: str, company: str) -> str:
    """Remove the exact job title / company name (allowed as names, never as claims)."""
    for name in sorted({job_title.strip(), company.strip()} - {""}, key=len, reverse=True):
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    return text


def check_cover_letter(doc: dict, profile: VerifiedProfile, *, job_description: str = "") -> list[str]:
    """Every paragraph may only use numbers from the facts it cites and words the verified facts support."""
    facts = profile.by_key()
    job = doc.get("job", {})
    title, company = str(job.get("title", "")), str(job.get("company", ""))
    corpus = profile_corpus(profile, job_text=job_description)
    violations: list[str] = []
    for paragraph in doc.get("paragraphs", []):
        cited = [facts.get(key) for key in paragraph.get("facts", [])]
        if any(fact is None for fact in cited):
            violations.append("paragraph cites a fact that is not verified")
            continue
        source = "\n".join(text for fact in cited if fact for text in fact_strings(fact))
        text = _strip_target(paragraph.get("text", ""), title, company)
        violations.extend(check_text(text, corpus, source=source))
    for line in [doc.get("greeting", ""), *doc.get("signature", [])[:1]]:
        violations.extend(check_text(_strip_target(line, title, company), corpus))
    return sorted(set(violations))
