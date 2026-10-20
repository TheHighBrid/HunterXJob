"""Deterministic, fact-only résumé and cover-letter builders.

Selection: only *verified* facts are used. Facts are ordered (never edited)
by relevance to the job: skills named in the posting first, then
achievements that mention those skills or share keywords with the posting.
Experience is reverse-chronological, except that for finance-domain targets
(banking, fraud, collections, credit, compliance, KYC/AML, client support at a
financial institution) finance roles lead and other roles follow, each group
still reverse-chronological. Nothing is dropped, only reordered.
Employer names, titles, dates, degrees and metrics are copied verbatim from
the facts. Every generated line records the fact it came from, so the
truthfulness guard (``app.truth_guard``) can check it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.profile import Fact, VerifiedProfile
from app.truth_guard import COMMON_WORDS, format_date

MAX_BULLETS_PER_ROLE = 4
MAX_SKILLS = 14
MAX_PROJECTS = 2
CONTACT_ORDER = ("email", "phone", "location", "linkedin_url", "github_url", "website_url")


@dataclass(frozen=True, slots=True)
class JobContext:
    title: str
    company: str
    location: str = ""
    description: str = ""

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.description}"


def _term_re(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", re.IGNORECASE)


def mentions(text: str, term: str) -> bool:
    return bool(term.strip()) and bool(_term_re(term.strip()).search(text or ""))


def _keywords(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z][a-z0-9+#-]{3,}", (text or "").lower()) if word not in COMMON_WORDS}


def skill_names(fact: Fact) -> list[str]:
    return [fact.data["name"], *fact.data.get("aliases", [])]


def matched_skills(profile: VerifiedProfile, job: JobContext) -> list[Fact]:
    return [fact for fact in profile.of("skill") if any(mentions(job.text, name) for name in skill_names(fact))]


def achievement_score(fact: Fact, job: JobContext, matched: list[Fact]) -> float:
    text = fact.data["text"]
    tagged = {name.lower() for name in fact.data.get("skills", [])}
    skill_hits = sum(
        1 for skill in matched
        if any(name.lower() in tagged or mentions(text, name) for name in skill_names(skill))
    )
    overlap = len(_keywords(text) & _keywords(job.text))
    return skill_hits * 3 + overlap + (0.5 if fact.data.get("metrics") else 0)


def _ranked(facts: list[Fact], job: JobContext, matched: list[Fact]) -> list[tuple[float, Fact]]:
    scored = [(achievement_score(fact, job, matched), index, fact) for index, fact in enumerate(facts)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(score, fact) for score, _, fact in scored]


def date_range(data: dict[str, Any]) -> str:
    start = format_date(data.get("start"))
    end = "Present" if data.get("current") and not data.get("end") else format_date(data.get("end"))
    return " \u2013 ".join(part for part in (start, end) if part)


def _recency(fact: Fact) -> tuple[str, str]:
    data = fact.data
    end = "9999" if data.get("current") and not data.get("end") else data.get("end") or data.get("start") or ""
    return end, data.get("start") or ""


def chronological(facts: list[Fact]) -> list[Fact]:
    return sorted(facts, key=_recency, reverse=True)


def _bullets(profile: VerifiedProfile, parent: Fact, job: JobContext, matched: list[Fact]) -> list[dict[str, Any]]:
    ranked = _ranked(profile.achievements_for(parent.key), job, matched)[:MAX_BULLETS_PER_ROLE]
    return [{"text": fact.data["text"], "fact": fact.key, "score": score} for score, fact in ranked]


# ------------------------------------------------------- finance-first ordering

# Title terms that make the target a finance-domain role on their own. A
# trailing "*" matches any suffix ("collection*" -> collections, collector).
_FINANCE_ROLE_TERMS = (
    "fraud*", "collection*", "collector*", "credit*", "compliance", "kyc", "aml", "know your customer",
    "anti-money", "money laundering", "financial crime*", "sanction*", "transaction monitoring", "dispute*",
    "account protection",
    "chargeback*", "underwrit*", "loan*", "lending", "mortgage*", "bank*", "teller*", "risk",
)
# Client-facing titles that count as finance-domain only in a financial context.
_CLIENT_SUPPORT_TERMS = (
    "client*", "customer*", "member*", "support", "service*", "care", "cx", "contact cent*", "call cent*",
    "onboarding",
)
# The posting's context is financial with one strong term anywhere, a weak term
# in the title or company name, or several weak terms in the description (an
# incidental "bank account" or "payment" in a non-finance posting is not enough).
_FINANCE_CONTEXT_STRONG = (
    "banking", "financial institution*", "financial service*", "fintech", "credit union*", "wealth management",
)
_FINANCE_CONTEXT_WEAK = ("bank*", "payment*", "credit card*", "lending", "lender*", "loan*", "mortgage*")
_WEAK_CONTEXT_HITS = 3
# Employers that are financial institutions (name terms or well-known names).
_FINANCE_EMPLOYER_TERMS = (
    "*bank*", "banque", "credit union", "caisse*", "trust", "financ*", "payments", "lending",
    "bmo", "rbc", "td", "cibc", "hsbc", "desjardins", "tangerine", "simplii", "atb", "laurentian",
    "american express", "amex", "capital one", "visa", "mastercard", "interac",
)


def _terms_re(terms: tuple[str, ...]) -> re.Pattern[str]:
    parts = []
    for term in terms:
        lead = r"" if term.startswith("*") else r"(?<![a-z0-9])"
        core = re.escape(term.strip("*"))
        tail = r"[a-z0-9-]*" if term.endswith("*") else r"(?![a-z0-9])"
        parts.append(lead + core + tail)
    return re.compile("|".join(parts), re.IGNORECASE)


_FINANCE_ROLE_RE = _terms_re(_FINANCE_ROLE_TERMS)
_CLIENT_SUPPORT_RE = _terms_re(_CLIENT_SUPPORT_TERMS)
_FINANCE_CONTEXT_STRONG_RE = _terms_re(_FINANCE_CONTEXT_STRONG)
_FINANCE_CONTEXT_WEAK_RE = _terms_re(_FINANCE_CONTEXT_WEAK)
_FINANCE_EMPLOYER_RE = _terms_re(_FINANCE_EMPLOYER_TERMS)


def is_finance_target(job: JobContext) -> bool:
    """True for banking/fraud/collections/credit/compliance/KYC/AML roles and client support in finance."""
    if _FINANCE_ROLE_RE.search(job.title or ""):
        return True
    if not _CLIENT_SUPPORT_RE.search(job.title or ""):
        return False
    heading = f"{job.title}\n{job.company}"
    if _FINANCE_CONTEXT_STRONG_RE.search(f"{heading}\n{job.description}") or _FINANCE_CONTEXT_WEAK_RE.search(heading):
        return True
    return len(_FINANCE_CONTEXT_WEAK_RE.findall(job.description or "")) >= _WEAK_CONTEXT_HITS


def is_finance_employment(fact: Fact) -> bool:
    """A role at a financial institution, or a finance-domain title anywhere (e.g. a fraud analyst at a fintech)."""
    return bool(_FINANCE_EMPLOYER_RE.search(fact.data.get("employer", ""))
                or _FINANCE_ROLE_RE.search(fact.data.get("title", "")))


def ordered_employment(profile: VerifiedProfile, job: JobContext) -> list[Fact]:
    """Reverse-chronological; for finance targets, finance roles first (stable, so each group stays chronological)."""
    facts = chronological(profile.of("employment"))
    if not is_finance_target(job):
        return facts
    return sorted(facts, key=lambda fact: 0 if is_finance_employment(fact) else 1)


def _experience(profile: VerifiedProfile, job: JobContext, matched: list[Fact]) -> list[dict[str, Any]]:
    out = []
    for fact in ordered_employment(profile, job):
        data = fact.data
        out.append({
            "fact": fact.key, "title": data["title"], "employer": data["employer"], "location": data.get("location", ""),
            "start": data.get("start"), "end": data.get("end"), "current": bool(data.get("current")),
            "dates": date_range(data), "bullets": _bullets(profile, fact, job, matched),
        })
    return out


def _projects(profile: VerifiedProfile, job: JobContext, matched: list[Fact]) -> list[dict[str, Any]]:
    scored = []
    for index, fact in enumerate(profile.of("project")):
        bullets = _bullets(profile, fact, job, matched)
        project_text = " ".join([fact.data.get("summary", ""), *fact.data.get("skills", [])])
        # Relevance only (the metrics bonus does not make a project relevant).
        score = sum(int(item["score"]) for item in bullets) + len(_keywords(project_text) & _keywords(job.text))
        if score > 0:
            scored.append((score, index, fact, bullets))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{
        "fact": fact.key, "name": fact.data["name"], "role": fact.data.get("role", ""),
        "start": fact.data.get("start"), "end": fact.data.get("end"), "dates": date_range(fact.data),
        "bullets": bullets,
    } for _, _, fact, bullets in scored[:MAX_PROJECTS]]


def _skills(profile: VerifiedProfile, matched: list[Fact]) -> list[dict[str, Any]]:
    matched_keys = {fact.key for fact in matched}
    ordered = matched + [fact for fact in profile.of("skill") if fact.key not in matched_keys]
    return [{"text": fact.data["name"], "fact": fact.key, "matched": fact.key in matched_keys} for fact in ordered[:MAX_SKILLS]]


def _education(profile: VerifiedProfile) -> list[dict[str, Any]]:
    return [{
        "fact": fact.key, "degree": fact.data["degree"], "field_of_study": fact.data.get("field_of_study", ""),
        "institution": fact.data["institution"], "location": fact.data.get("location", ""),
        "start": fact.data.get("start"), "end": fact.data.get("end"), "dates": date_range(fact.data),
    } for fact in chronological(profile.of("education"))]


def _certification_text(data: dict[str, Any]) -> str:
    text = data["name"] + (f" — {data['issuer']}" if data.get("issuer") else "")
    return text + (f" ({format_date(data['date'])})" if data.get("date") else "")


def _language_text(data: dict[str, Any]) -> str:
    return data["name"] + (f" ({data['proficiency']})" if data.get("proficiency") else "")


def build_resume(profile: VerifiedProfile, job: JobContext) -> dict[str, Any]:
    matched = matched_skills(profile, job)
    contact = profile.contact
    contact_facts = {fact.data["field"]: fact.key for fact in profile.of("contact")}
    summary = next(iter(profile.of("summary")), None)
    matched_keys = {fact.key for fact in matched}
    certifications = sorted(profile.of("certification"),
                            key=lambda fact: 0 if any(mentions(fact.data["name"], name) for skill in matched
                                                      for name in skill_names(skill)) or fact.key in matched_keys else 1)
    return {
        "kind": "resume",
        "name": profile.full_name,
        "contact": [{"text": contact[name], "fact": contact_facts[name]} for name in CONTACT_ORDER if name in contact],
        "summary": {"text": summary.data["text"], "fact": summary.key} if summary else None,
        "skills": _skills(profile, matched),
        "experience": _experience(profile, job, matched),
        "projects": _projects(profile, job, matched),
        "education": _education(profile),
        "certifications": [{"text": _certification_text(fact.data), "fact": fact.key} for fact in certifications],
        "languages": [{"text": _language_text(fact.data), "fact": fact.key} for fact in profile.of("language")],
        "job": {"title": job.title, "company": job.company, "description": job.description[:4000]},
        "matched_skills": [fact.data["name"] for fact in matched],
        "experience_order": "finance_first" if is_finance_target(job) else "chronological",
    }


Line = tuple[str, str]


def _entry_lines(entries: list[dict[str, Any]], primary: str, secondary: str) -> list[Line]:
    lines: list[Line] = []
    for entry in entries:
        head = entry[primary] + (f" — {entry[secondary]}" if entry.get(secondary) else "")
        lines.append(("entry", head + (f", {entry['location']}" if entry.get("location") else "")))
        if entry.get("dates"):
            lines.append(("dates", entry["dates"]))
        lines += [("bullet", bullet["text"]) for bullet in entry["bullets"]]
    return lines


def _education_lines(entries: list[dict[str, Any]]) -> list[Line]:
    lines: list[Line] = []
    for entry in entries:
        degree = entry["degree"] + (f", {entry['field_of_study']}" if entry.get("field_of_study") else "")
        lines.append(("entry", f"{degree} — {entry['institution']}"))
        if entry.get("dates"):
            lines.append(("dates", entry["dates"]))
    return lines


def _section(heading: str, lines: list[Line]) -> list[Line]:
    return [("heading", heading), *lines] if lines else []


def resume_lines(doc: dict[str, Any]) -> list[Line]:
    """(style, text) lines of a résumé, shared by the text, PDF and DOCX renderers."""
    summary = [("body", doc["summary"]["text"])] if doc.get("summary") else []
    skills = [("body", ", ".join(item["text"] for item in doc["skills"]))] if doc["skills"] else []
    return [
        ("name", doc["name"]), ("contact", " | ".join(item["text"] for item in doc["contact"])),
        *_section("Summary", summary),
        *_section("Skills", skills),
        *_section("Experience", _entry_lines(doc["experience"], "title", "employer")),
        *_section("Projects", _entry_lines(doc["projects"], "name", "role")),
        *_section("Education", _education_lines(doc["education"])),
        *_section("Certifications", [("body", item["text"]) for item in doc["certifications"]]),
        *_section("Languages", [("body", item["text"]) for item in doc["languages"]]),
    ]


_TEXT_FORMAT = {"name": "{}", "heading": "\n{}", "bullet": "• {}"}


def resume_text(doc: dict[str, Any]) -> str:
    out = []
    for style, text in resume_lines(doc):
        value = text.upper() if style in {"name", "heading"} else text
        out.append(_TEXT_FORMAT.get(style, "{}").format(value))
    return "\n".join(out).strip() + "\n"


# ----------------------------------------------------------------- cover letter


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _summary_sentence(text: str) -> str:
    """'Bilingual analyst with...' -> 'I am a bilingual analyst with...' (first-person summaries are kept)."""
    text = _sentence(text)
    first = text.split(" ", 1)[0]
    if first in {"I", "I'm", "I\u2019m", "My"} or len(first) < 2 or not first[1:].islower():
        return text
    article = "an" if first[0].lower() in "aeiou" else "a"
    return f"I am {article} {text[0].lower()}{text[1:]}"


def _human_list(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _experience_paragraphs(resume: dict[str, Any]) -> list[dict[str, Any]]:
    paragraphs = []
    for entry in resume["experience"][:2]:
        bullets = entry["bullets"][:2]
        if not bullets:
            continue
        verb = "has included" if entry.get("current") else "included"
        dates = entry["dates"].replace("Present", "present")
        intro = f"As {entry['title']} at {entry['employer']} ({dates}), my work {verb} the following."
        text = " ".join([intro, *[_sentence(bullet["text"]) for bullet in bullets]])
        paragraphs.append({"text": text, "facts": [entry["fact"], *[bullet["fact"] for bullet in bullets]]})
    return paragraphs


def build_cover_letter(profile: VerifiedProfile, job: JobContext, resume: dict[str, Any]) -> dict[str, Any]:
    target = f"the {job.title} position" + (f" at {job.company}" if job.company else "")
    paragraphs: list[dict[str, Any]] = []
    opening = f"I am applying for {target}."
    summary = resume.get("summary")
    paragraphs.append({"text": f"{opening} {_summary_sentence(summary['text'])}" if summary else opening,
                       "facts": [summary["fact"]] if summary else []})
    paragraphs += _experience_paragraphs(resume)
    matched = [item for item in resume["skills"] if item["matched"]][:5]
    if matched:
        paragraphs.append({
            "text": f"The requirements in the posting match several of my core skills: {_human_list([item['text'] for item in matched])}.",
            "facts": [item["fact"] for item in matched],
        })
    education = resume["education"][:1]
    if education:
        entry = education[0]
        field = f" in {entry['field_of_study']}" if entry.get("field_of_study") else ""
        paragraphs.append({"text": f"I hold a {entry['degree']}{field} from {entry['institution']}.", "facts": [entry["fact"]]})
    paragraphs.append({"text": f"Thank you for considering my application. I would welcome the chance to discuss {target}.",
                       "facts": []})
    contact = [item for item in resume["contact"] if item["fact"].endswith((":email", ":phone"))]
    return {
        "kind": "cover_letter",
        "name": resume["name"],
        "greeting": f"Dear Hiring Team at {job.company}," if job.company else "Dear Hiring Team,",
        "paragraphs": paragraphs,
        "closing": "Sincerely,",
        "signature": [resume["name"], *[item["text"] for item in contact]],
        "job": {"title": job.title, "company": job.company},
    }


def cover_letter_body(doc: dict[str, Any]) -> str:
    return "\n\n".join(paragraph["text"] for paragraph in doc["paragraphs"])


def cover_letter_text(doc: dict[str, Any]) -> str:
    parts = [doc["greeting"], cover_letter_body(doc), doc["closing"], "\n".join(doc["signature"])]
    return "\n\n".join(parts).strip() + "\n"
