"""Verified-facts candidate profile.

The profile is a set of small facts (one employer, one degree, one skill,
one achievement...). Every fact records where it came from (``source`` and
``provenance``) and whether the owner has confirmed it (``verified``).

Rules:

* Only verified facts are used to build résumés, cover letters and form
  answers. Nothing else is ever added.
* Facts parsed from a résumé always start unverified.
* Editing a fact's content makes it unverified again unless the same edit
  explicitly re-verifies it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProfileFact

Category = Literal[
    "contact", "summary", "work_authorization", "employment", "education",
    "skill", "certification", "language", "project", "achievement",
]
CATEGORIES: tuple[str, ...] = (
    "contact", "summary", "work_authorization", "employment", "education",
    "skill", "certification", "language", "project", "achievement",
)
CONTACT_FIELDS = (
    "first_name", "last_name", "preferred_name", "email", "phone", "location",
    "linkedin_url", "github_url", "website_url",
)
SOURCES = ("profile_file", "resume_text", "resume_pdf", "resume_docx", "manual")
MAX_IMPORT_BYTES = 512_000

_DATE_RE = re.compile(r"^\d{4}(?:-(?:0[1-9]|1[0-2]))?$")
_Text = Field(default="", max_length=600)


def _date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not _DATE_RE.match(text):
        raise ValueError("dates must be YYYY or YYYY-MM")
    return text


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContactData(_Fact):
    field: Literal[
        "first_name", "last_name", "preferred_name", "email", "phone", "location",
        "linkedin_url", "github_url", "website_url",
    ]
    value: str = Field(min_length=1, max_length=300)


class SummaryData(_Fact):
    text: str = Field(min_length=1, max_length=1200)


class WorkAuthorizationData(_Fact):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    authorized: bool
    requires_sponsorship: bool | None = None
    status: str = Field(default="", max_length=120)

    @field_validator("country", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return str(value).strip().upper() if value is not None else value


class _Dated(_Fact):
    start: str | None = None
    end: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> str | None:
        return _date(value)


class EmploymentData(_Dated):
    employer: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    current: bool = False
    summary: str = _Text


class EducationData(_Dated):
    institution: str = Field(min_length=1, max_length=200)
    degree: str = Field(min_length=1, max_length=200)
    field_of_study: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)


class SkillData(_Fact):
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    level: str = Field(default="", max_length=40)
    years: int | None = Field(default=None, ge=0, le=60)


class CertificationData(_Fact):
    name: str = Field(min_length=1, max_length=200)
    issuer: str = Field(default="", max_length=200)
    date: str | None = None
    expires: str | None = None
    credential_id: str = Field(default="", max_length=120)

    @field_validator("date", "expires", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> str | None:
        return _date(value)


class LanguageData(_Fact):
    name: str = Field(min_length=1, max_length=80)
    proficiency: str = Field(default="", max_length=80)


class ProjectData(_Dated):
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=300)
    summary: str = _Text
    skills: list[str] = Field(default_factory=list, max_length=30)


class AchievementData(_Fact):
    text: str = Field(min_length=1, max_length=600)
    metrics: list[str] = Field(default_factory=list, max_length=10)
    employment: str | None = Field(default=None, max_length=160)
    project: str | None = Field(default=None, max_length=160)
    skills: list[str] = Field(default_factory=list, max_length=30)


_SCHEMAS: dict[str, type[_Fact]] = {
    "contact": ContactData,
    "summary": SummaryData,
    "work_authorization": WorkAuthorizationData,
    "employment": EmploymentData,
    "education": EducationData,
    "skill": SkillData,
    "certification": CertificationData,
    "language": LanguageData,
    "project": ProjectData,
    "achievement": AchievementData,
}


class ProfileError(ValueError):
    """Invalid profile input (shown to the owner)."""


def validate_fact(category: str, data: dict[str, Any]) -> dict[str, Any]:
    schema = _SCHEMAS.get(category)
    if schema is None:
        raise ProfileError(f"unknown fact category {category!r}")
    try:
        model = schema.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or category
        raise ProfileError(f"{category}: {where}: {first.get('msg', 'invalid')}") from exc
    return model.model_dump()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60] or "item"


def fact_key(category: str, data: dict[str, Any], explicit_id: str | None = None) -> str:
    """Stable identity of a fact, so re-importing the same file updates in place."""
    if explicit_id:
        return f"{category}:{slug(explicit_id)}"
    parts = {
        "contact": lambda: data["field"],
        "summary": lambda: "main",
        "work_authorization": lambda: data["country"],
        "employment": lambda: f"{slug(data['employer'])}-{data.get('start') or 'na'}",
        "education": lambda: f"{slug(data['institution'])}-{slug(data['degree'])}",
        "project": lambda: slug(data["name"]),
        "achievement": lambda: hashlib.sha256(data["text"].encode("utf-8")).hexdigest()[:12],
    }
    part = parts.get(category, lambda: slug(str(data.get("name", ""))))()
    return f"{category}:{part}"


# ---------------------------------------------------------------------- drafts


@dataclass(slots=True)
class FactDraft:
    category: str
    data: dict[str, Any]
    provenance: str
    verified: bool = False
    key: str = ""
    position: int = 0

    def __post_init__(self) -> None:
        self.data = validate_fact(self.category, self.data)
        if not self.key:
            self.key = fact_key(self.category, self.data)


@dataclass(slots=True)
class ImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    verified: int = 0
    unverified: int = 0
    keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created, "updated": self.updated, "unchanged": self.unchanged,
            "verified": self.verified, "unverified": self.unverified,
            "keys": self.keys, "warnings": self.warnings,
        }


def _flag(entry: dict[str, Any], default: bool) -> bool:
    value = entry.pop("verified", default)
    return value is True


def _entries(document: dict[str, Any], name: str) -> list[Any]:
    value = document.get(name)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ProfileError(f"{name} must be a list")


def _named(entry: Any, key: str = "name") -> dict[str, Any]:
    if isinstance(entry, str):
        return {key: entry}
    if isinstance(entry, dict):
        return dict(entry)
    raise ProfileError("profile entries must be mappings or strings")


class _Collector:
    """Turns a profile document (parsed YAML/JSON) into fact drafts."""

    def __init__(self, origin: str, verified_default: bool) -> None:
        self.origin = origin
        self.verified_default = verified_default
        self.drafts: list[FactDraft] = []

    def add(self, category: str, entry: dict[str, Any], where: str, explicit_id: str | None = None) -> FactDraft:
        verified = _flag(entry, self.verified_default)
        data = validate_fact(category, entry)
        draft = FactDraft(
            category=category, data=data, provenance=f"{self.origin}#{where}", verified=verified,
            key=fact_key(category, data, explicit_id), position=len(self.drafts),
        )
        self.drafts.append(draft)
        return draft

    def achievements(self, items: Any, parent: FactDraft, where: str) -> None:
        if not isinstance(items, list):
            raise ProfileError(f"{where}.achievements must be a list")
        link = "employment" if parent.category == "employment" else "project"
        for index, item in enumerate(items):
            entry = _named(item, "text")
            entry.setdefault(link, parent.key)
            entry.setdefault("verified", parent.verified)
            self.add("achievement", entry, f"{where}.achievements[{index}]")

    def nested(self, category: str, items: list[Any], name: str) -> None:
        for index, item in enumerate(items):
            entry = _named(item)
            explicit = entry.pop("id", None)
            children = entry.pop("achievements", None)
            parent = self.add(category, entry, f"{name}[{index}]", str(explicit) if explicit else None)
            if children:
                self.achievements(children, parent, f"{name}[{index}]")


def _contact_entries(collector: _Collector, contact: Any) -> None:
    if contact is None:
        return
    if not isinstance(contact, dict):
        raise ProfileError("contact must be a mapping")
    contact = dict(contact)
    verified = contact.pop("verified", collector.verified_default) is True
    for name, value in contact.items():
        if value in (None, ""):
            continue
        if name not in CONTACT_FIELDS:
            raise ProfileError(f"contact: unknown field {name!r} (allowed: {', '.join(CONTACT_FIELDS)})")
        collector.add("contact", {"field": name, "value": str(value), "verified": verified}, f"contact.{name}")


def drafts_from_document(document: Any, *, origin: str, verified_default: bool = False) -> list[FactDraft]:
    """Parse a profile document (see ``examples/profile.example.yaml``)."""
    if not isinstance(document, dict):
        raise ProfileError("the profile file must contain a mapping at the top level")
    known = {"verified", "contact", "summary", "work_authorization", "employment", "education",
             "skills", "certifications", "languages", "projects", "achievements"}
    unknown = sorted(set(document) - known)
    if unknown:
        raise ProfileError(f"unknown top-level keys: {', '.join(unknown)}")
    collector = _Collector(origin, document.get("verified", verified_default) is True)
    _contact_entries(collector, document.get("contact"))
    summary = document.get("summary")
    if summary:
        collector.add("summary", _named(summary, "text"), "summary")
    for index, entry in enumerate(_entries(document, "work_authorization")):
        collector.add("work_authorization", _named(entry, "country"), f"work_authorization[{index}]")
    collector.nested("employment", _entries(document, "employment"), "employment")
    collector.nested("education", _entries(document, "education"), "education")
    simple = (("skills", "skill"), ("certifications", "certification"), ("languages", "language"))
    for name, category in simple:
        for index, entry in enumerate(_entries(document, name)):
            collector.add(category, _named(entry), f"{name}[{index}]")
    collector.nested("project", _entries(document, "projects"), "projects")
    for index, entry in enumerate(_entries(document, "achievements")):
        collector.add("achievement", _named(entry, "text"), f"achievements[{index}]")
    return collector.drafts


def parse_profile_text(content: str, fmt: Literal["yaml", "json"]) -> Any:
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ProfileError("profile file is too large")
    try:
        if fmt == "json":
            return json.loads(content)
        import yaml

        return yaml.safe_load(content)
    except (ValueError, ImportError) as exc:
        raise ProfileError(f"could not parse the {fmt} profile: {exc}") from exc
    except Exception as exc:  # yaml.YAMLError and friends
        raise ProfileError(f"could not parse the {fmt} profile: {exc.__class__.__name__}") from exc


# ----------------------------------------------------------------------- store


def _now() -> datetime:
    return datetime.now(UTC)


def _apply_draft(db: Session, draft: FactDraft, source: str, result: ImportResult) -> None:
    row = db.execute(select(ProfileFact).where(ProfileFact.key == draft.key)).scalar_one_or_none()
    encoded = json.dumps(draft.data, sort_keys=True)
    if row is not None and row.verified and row.data_json != encoded and source.startswith("resume_"):
        # A parsed résumé never overwrites something the owner already verified.
        result.warnings.append(f"kept verified fact {draft.key}; the résumé suggests: {encoded[:160]}")
        result.unchanged += 1
        result.keys.append(draft.key)
        result.verified += 1
        return
    if row is None:
        row = ProfileFact(key=draft.key, category=draft.category, data_json=encoded, verified=draft.verified,
                          source=source, provenance=draft.provenance, position=draft.position,
                          verified_at=_now() if draft.verified else None)
        db.add(row)
        result.created += 1
    elif row.data_json != encoded:
        row.data_json = encoded
        row.verified = draft.verified
        row.verified_at = _now() if draft.verified else None
        row.source = source
        row.provenance = draft.provenance
        row.position = draft.position
        result.updated += 1
    else:
        if draft.verified and not row.verified:
            row.verified = True
            row.verified_at = _now()
        result.unchanged += 1
    result.keys.append(draft.key)
    if row.verified:
        result.verified += 1
    else:
        result.unverified += 1


def store_drafts(db: Session, drafts: list[FactDraft], source: str) -> ImportResult:
    if source not in SOURCES:
        raise ProfileError(f"unknown source {source!r}")
    result = ImportResult()
    seen: set[str] = set()
    for draft in drafts:
        if draft.key in seen:
            result.warnings.append(f"duplicate fact {draft.key} ignored")
            continue
        seen.add(draft.key)
        _apply_draft(db, draft, source, result)
    db.commit()
    return result


def import_profile_document(db: Session, document: Any, *, origin: str) -> ImportResult:
    """Import a structured profile (YAML/JSON). Facts are verified only where the file says so."""
    return store_drafts(db, drafts_from_document(document, origin=origin), "profile_file")


def fact_view(row: ProfileFact) -> dict[str, Any]:
    from app.models import iso_utc

    return {
        "id": row.id, "key": row.key, "category": row.category, "data": json.loads(row.data_json),
        "verified": row.verified, "source": row.source, "provenance": row.provenance,
        "verified_at": iso_utc(row.verified_at), "updated_at": iso_utc(row.updated_at),
    }


def list_facts(db: Session, *, verified_only: bool = False) -> list[ProfileFact]:
    query = select(ProfileFact)
    if verified_only:
        query = query.where(ProfileFact.verified.is_(True))
    rows = list(db.execute(query).scalars())
    order = {name: index for index, name in enumerate(CATEGORIES)}
    rows.sort(key=lambda row: (order.get(row.category, 99), row.position, row.key))
    return rows


def get_fact(db: Session, fact_id: str) -> ProfileFact:
    row = db.get(ProfileFact, fact_id)
    if row is None:
        raise LookupError("profile fact not found")
    return row


def set_verified(db: Session, fact_id: str, verified: bool) -> ProfileFact:
    row = get_fact(db, fact_id)
    row.verified = verified
    row.verified_at = _now() if verified else None
    db.commit()
    db.refresh(row)
    return row


def edit_fact(db: Session, fact_id: str, data: dict[str, Any], *, verified: bool = False) -> ProfileFact:
    """Replace a fact's content. The fact is unverified afterwards unless ``verified`` is True."""
    row = get_fact(db, fact_id)
    clean = validate_fact(row.category, data)
    encoded = json.dumps(clean, sort_keys=True)
    if encoded != row.data_json:
        row.data_json = encoded
        row.source = "manual"
        row.provenance = "edited by owner"
    row.verified = verified
    row.verified_at = _now() if verified else None
    db.commit()
    db.refresh(row)
    return row


def add_fact(db: Session, category: str, data: dict[str, Any], *, verified: bool = False) -> ProfileFact:
    draft = FactDraft(category=category, data=data, provenance="added by owner", verified=verified)
    if db.execute(select(ProfileFact).where(ProfileFact.key == draft.key)).scalar_one_or_none() is not None:
        raise ProfileError(f"a fact with key {draft.key} already exists; edit it instead")
    position = len(list_facts(db))
    row = ProfileFact(key=draft.key, category=category, data_json=json.dumps(draft.data, sort_keys=True),
                      verified=verified, source="manual", provenance=draft.provenance, position=position,
                      verified_at=_now() if verified else None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def remove_fact(db: Session, fact_id: str) -> None:
    db.delete(get_fact(db, fact_id))
    db.commit()


# ------------------------------------------------------------ verified profile


@dataclass(slots=True)
class Fact:
    key: str
    category: str
    data: dict[str, Any]


@dataclass(slots=True)
class VerifiedProfile:
    """Only the verified facts, grouped for material generation."""

    facts: list[Fact] = field(default_factory=list)

    def of(self, category: str) -> list[Fact]:
        return [fact for fact in self.facts if fact.category == category]

    def by_key(self) -> dict[str, Fact]:
        return {fact.key: fact for fact in self.facts}

    @property
    def contact(self) -> dict[str, str]:
        return {fact.data["field"]: fact.data["value"] for fact in self.of("contact")}

    @property
    def full_name(self) -> str:
        contact = self.contact
        first = contact.get("preferred_name") or contact.get("first_name", "")
        return " ".join(part for part in (first, contact.get("last_name", "")) if part)

    def achievements_for(self, key: str) -> list[Fact]:
        return [fact for fact in self.of("achievement")
                if key in (fact.data.get("employment"), fact.data.get("project"))]

    def standalone_achievements(self) -> list[Fact]:
        return [fact for fact in self.of("achievement")
                if not (fact.data.get("employment") or fact.data.get("project"))]

    @property
    def sha256(self) -> str:
        encoded = json.dumps([[fact.key, fact.data] for fact in self.facts], sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def empty(self) -> bool:
        return not self.facts


def verified_profile(db: Session) -> VerifiedProfile:
    facts = [Fact(row.key, row.category, json.loads(row.data_json)) for row in list_facts(db, verified_only=True)]
    keys = {fact.key for fact in facts}
    # An achievement tied to an unverified employer/project cannot be attributed; drop it.
    kept = [fact for fact in facts if fact.category != "achievement" or _link_ok(fact, keys)]
    return VerifiedProfile(kept)


def _link_ok(fact: Fact, keys: set[str]) -> bool:
    links = [fact.data.get("employment"), fact.data.get("project")]
    return all(link in keys for link in links if link)


def readiness(profile: VerifiedProfile) -> list[str]:
    """What is still missing before materials can be generated."""
    missing = []
    contact = profile.contact
    if not (contact.get("first_name") or contact.get("preferred_name")) or not contact.get("last_name"):
        missing.append("verified first and last name (contact)")
    if not contact.get("email"):
        missing.append("verified email (contact)")
    if not (profile.of("employment") or profile.of("education")):
        missing.append("at least one verified employment or education entry")
    return missing
