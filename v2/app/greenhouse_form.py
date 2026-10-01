"""Read-only extraction of a real Greenhouse application form.

The public Greenhouse Job Board API exposes the application questions for a
posting at ``GET /v1/boards/{board}/jobs/{id}?questions=true``. This module
turns that payload into :class:`~app.form_engine.FormControl` objects so the
dry-run pipeline plans against the employer's actual form instead of a
sample.

Safety contract:

* Only HTTP GET requests are made, only to ``boards-api.greenhouse.io``,
  and redirects are not followed.
* Nothing is submitted, uploaded, or posted. There is no code path in this
  module that writes to Greenhouse.
* If the real form cannot be fetched or parsed, :class:`FormFetchError` is
  raised with a review reason code. Callers must surface it; there is no
  silent fallback to a sample form.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.answer_vault import SENSITIVE_KEYS
from app.form_engine import (
    AUTH_PATTERNS,
    LEGAL_PATTERNS,
    SENSITIVE_PATTERNS,
    ControlType,
    FormControl,
)

GREENHOUSE_API_HOST = "boards-api.greenhouse.io"
GREENHOUSE_EMBED_HOST = "job-boards.greenhouse.io"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_JOB_ID_RE = re.compile(r"^[0-9]{1,20}$")


class FormFetchError(RuntimeError):
    """The real application form could not be obtained.

    ``reason_code`` is a review-queue reason: ``form_unavailable`` when the
    posting is gone or unsupported, ``form_fetch_failed`` for transport or
    parsing failures.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class GreenhouseJobRef:
    board: str
    job_id: str

    def __post_init__(self) -> None:
        if not _TOKEN_RE.match(self.board or ""):
            raise ValueError(f"invalid Greenhouse board token: {self.board!r}")
        if not _JOB_ID_RE.match(self.job_id or ""):
            raise ValueError(f"invalid Greenhouse job id: {self.job_id!r}")

    @property
    def api_url(self) -> str:
        return f"https://{GREENHOUSE_API_HOST}/v1/boards/{self.board}/jobs/{self.job_id}"

    @property
    def embed_url(self) -> str:
        return f"https://{GREENHOUSE_EMBED_HOST}/embed/job_app?for={self.board}&token={self.job_id}"

    @property
    def vault_scopes(self) -> list[str]:
        return [f"greenhouse:{self.board}", f"greenhouse:{self.board}:{self.job_id}"]


@dataclass(slots=True)
class RealForm:
    """An application form obtained from the live employer posting."""

    platform: str
    source: str
    url: str
    title: str
    controls: list[FormControl]
    vault_scopes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    handoff: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> dict[str, Any]:
        sections: dict[str, int] = {}
        for control in self.controls:
            sections[control.section] = sections.get(control.section, 0) + 1
        return {
            "platform": self.platform,
            "source": self.source,
            "url": self.url,
            "title": self.title,
            "fetched_at": self.fetched_at,
            "fields_total": len(self.controls),
            "fields_required": sum(1 for control in self.controls if control.required),
            "sections": sections,
            "warnings": list(self.warnings),
            "handoff": self.handoff,
            "metadata": dict(self.metadata),
        }


def _valid_token(value: str | None) -> bool:
    return bool(value and _TOKEN_RE.match(value))


def _valid_job_id(value: str | None) -> bool:
    return bool(value and _JOB_ID_RE.match(value))


def parse_greenhouse_ref(
    url: str,
    *,
    board_hint: str | None = None,
    job_id_hint: str | None = None,
) -> GreenhouseJobRef | None:
    """Identify the Greenhouse board token and job id for a posting.

    Recognizes hosted board URLs (``boards.greenhouse.io/{token}/jobs/{id}``
    and ``job-boards.greenhouse.io/...``), embed URLs
    (``/embed/job_app?for={token}&token={id}``), and employer career pages
    carrying ``gh_jid`` when the board token is known from discovery.
    """
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    query = parse_qs(parsed.query)
    path_parts = [part for part in parsed.path.split("/") if part]

    if host.endswith("greenhouse.io"):
        if path_parts[:2] == ["embed", "job_app"]:
            token = (query.get("for") or [None])[0]
            job_id = (query.get("token") or [None])[0]
            if _valid_token(token) and _valid_job_id(job_id):
                return GreenhouseJobRef(token, job_id)
        if len(path_parts) >= 3 and path_parts[1] == "jobs":
            token, job_id = path_parts[0], path_parts[2]
            if _valid_token(token) and _valid_job_id(job_id):
                return GreenhouseJobRef(token, job_id)

    gh_jid = (query.get("gh_jid") or [None])[0]
    job_id = gh_jid if _valid_job_id(gh_jid) else job_id_hint
    if _valid_token(board_hint) and _valid_job_id(job_id):
        return GreenhouseJobRef(board_hint, job_id)  # type: ignore[arg-type]
    return None


def ref_for_job(job: Any) -> GreenhouseJobRef | None:
    """Build a ref from a discovered ``Job`` row.

    Greenhouse discovery stores the board token in ``company`` and the job id
    in ``external_id``; those are only trusted when ``source`` is greenhouse.
    """
    from_greenhouse = (getattr(job, "source", "") or "") == "greenhouse"
    return parse_greenhouse_ref(
        getattr(job, "url", "") or "",
        board_hint=getattr(job, "company", None) if from_greenhouse else None,
        job_id_hint=getattr(job, "external_id", None) if from_greenhouse else None,
    )


# --- question classification -------------------------------------------------

_STANDARD_FIELDS = {
    "first_name": ControlType.TEXT,
    "last_name": ControlType.TEXT,
    "preferred_name": ControlType.TEXT,
    "email": ControlType.EMAIL,
    "phone": ControlType.TEL,
    "resume": ControlType.FILE,
    "resume_text": ControlType.TEXTAREA,
    "cover_letter": ControlType.FILE,
    "cover_letter_text": ControlType.TEXTAREA,
}

_COUNTRY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ca", re.compile(
        r"\bcanad|\bontario\b|\bquebec\b|\bqu[ée]bec\b|\bbritish columbia\b|\balberta\b|\bmanitoba\b|"
        r"\bsaskatchewan\b|\bnova scotia\b|\bnew brunswick\b|\bnewfoundland\b|\bprince edward island\b|"
        r",\s*(?:on|qc|bc|ab|mb|sk|ns|nb|nl|pe)\b",
        re.IGNORECASE,
    )),
    ("us", re.compile(r"\bunited states\b|\bu\.s\.(?:a\.)?|\busa\b|\bus\b", re.IGNORECASE)),
    ("uk", re.compile(r"\bunited kingdom\b|\bu\.k\.|\buk\b|\bengland\b|\bbritain\b", re.IGNORECASE)),
)

_RESIDENCE_RE = re.compile(
    r"country of residence|current country|current location|your country|where you (?:currently )?(?:live|reside)",
    re.IGNORECASE,
)
_CONDITIONAL_RE = re.compile(
    r"\bif (?:you answered|yes|no|so|applicable|other|\"other\"|\u201cyes|\u201cother)|if [\"\u201c]?other[\"\u201d]? is selected",
    re.IGNORECASE,
)
_ROLE_LOCATION_RE = re.compile(
    r"this location|this (?:position|role|job)|the (?:position|role|job) is located|where (?:this|the) (?:position|role|job)",
    re.IGNORECASE,
)


def detect_country(text: str) -> str | None:
    """Return a single country code mentioned in ``text``, or None if zero or several."""
    hits = {code for code, pattern in _COUNTRY_PATTERNS if pattern.search(text or "")}
    return hits.pop() if len(hits) == 1 else None


def _country_scoped(base: str, label: str, job_location: str) -> str | None:
    """Scope a work-authorization style key to the country the question is about.

    A single global "Yes" must never be reused across countries, so the key
    always names the country explicitly. Questions that do not identify a
    country unambiguously get no shared key and therefore go to review unless
    the owner answered that specific question.
    """
    if _RESIDENCE_RE.search(label):
        return f"{base}_residence_country"
    country = detect_country(label)
    if country is None and _ROLE_LOCATION_RE.search(label):
        country = detect_country(job_location)
    return f"{base}_{country}" if country else None


def classify_question(label: str, job_location: str = "") -> str | None:
    """Map a custom question label to a canonical answer-vault key.

    Deterministic patterns only. Returns None for employer-specific or
    unrecognized questions; those must be answered per question.
    """
    text = " ".join((label or "").split()).lower()
    if not text:
        return None
    # Conditional follow-ups ("if you answered yes...") depend on another
    # answer and are never shared across questions.
    if _CONDITIONAL_RE.search(text):
        return None
    if "sponsor" in text:
        return _country_scoped("sponsorship", text, job_location)
    if re.search(r"(?:legally )?(?:eligible|authori[sz]ed|permitted|entitled) to work|work authori[sz]ation|right to work|work permit", text):
        return _country_scoped("work_authorization", text, job_location)
    if re.search(r"previous(?:ly)? (?:worked|employed|been employed)|worked (?:at|for) .* (?:before|previously|in the past)|"
                 r"(?:employed|engaged)[^?]* in the past|consulted for", text):
        return None
    if "linkedin" in text:
        return "linkedin_url"
    if "github" in text:
        return "github_url"
    if re.search(r"portfolio|personal website|\bwebsite\b", text):
        return "website_url"
    if "pronoun" in text:
        return "pronouns"
    if re.search(r"preferred (?:full |first )?name|name you.?d prefer", text):
        return "preferred_name"
    if re.search(r"salary|compensation expectation|pay expectation|desired (?:pay|compensation)", text):
        return "salary_expectation"
    if len(text) <= 60 and re.search(r"current (?:company|employer)", text):
        return "current_company"
    if len(text) <= 60 and re.search(r"current (?:job )?title|current role", text):
        return "current_title"
    if "relocat" in text:
        return "willing_to_relocate"
    if re.search(r"how did you hear|hear(?:d)? about", text):
        return "referral_source"
    if "country" in text and re.search(r"resid|located|\blive\b|based", text):
        return "country_of_residence"
    if re.search(r"time ?zone", text):
        return "time_zone"
    return None


_DEMOGRAPHIC_KEYS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gender", re.IGNORECASE), "gender"),
    (re.compile(r"sexual orientation", re.IGNORECASE), "sexual_orientation"),
    (re.compile(r"hispanic|latin", re.IGNORECASE), "hispanic_ethnicity"),
    (re.compile(r"race|ethnic", re.IGNORECASE), "race_ethnicity"),
    (re.compile(r"disab", re.IGNORECASE), "disability"),
    (re.compile(r"veteran", re.IGNORECASE), "veteran_status"),
    (re.compile(r"pronoun", re.IGNORECASE), "pronouns"),
)


def _demographic_key(label: str) -> str | None:
    for pattern, key in _DEMOGRAPHIC_KEYS:
        if pattern.search(label or ""):
            return key
    return None


def _looks_like(text: str, patterns: Iterable[str]) -> bool:
    lowered = (text or "").lower()
    return any(pattern in lowered for pattern in patterns)


def _is_sensitive_key(key: str | None) -> bool:
    if not key:
        return False
    return key in SENSITIVE_KEYS or key.startswith(("work_authorization", "sponsorship"))


_TYPE_MAP = {
    "input_text": ControlType.TEXT,
    "textarea": ControlType.TEXTAREA,
    "input_file": ControlType.FILE,
    "input_hidden": ControlType.HIDDEN,
    "multi_value_single_select": ControlType.SELECT,
    "multi_value_multi_select": ControlType.MULTISELECT,
}


def _control_from_field(
    field_data: dict[str, Any],
    *,
    label: str,
    required: bool,
    section: str,
    job_location: str,
    position: int,
) -> FormControl:
    raw_name = str(field_data.get("name") or "")
    name = raw_name.removesuffix("[]")
    raw_type = str(field_data.get("type") or "")
    control_type = _TYPE_MAP.get(raw_type, ControlType.UNKNOWN)
    if name in _STANDARD_FIELDS and control_type is ControlType.TEXT:
        control_type = _STANDARD_FIELDS[name]
    if section == "location" and name == "location":
        control_type = ControlType.AUTOCOMPLETE

    options: list[str] = []
    option_values: dict[str, str] = {}
    for value in field_data.get("values") or []:
        option_label = " ".join(str(value.get("label", "")).split())
        if option_label:
            options.append(option_label)
            option_values[option_label] = str(value.get("value", ""))

    if section == "eeoc":
        canonical = name or _demographic_key(label)
    elif name in _STANDARD_FIELDS or section == "location":
        canonical = name
    elif _looks_like(label, LEGAL_PATTERNS):
        # Legal declarations are answered per question, never from a shared key.
        canonical = None
    else:
        canonical = classify_question(label, job_location)

    voluntary = section == "eeoc"
    sensitive = (
        voluntary
        or _is_sensitive_key(canonical)
        or _looks_like(label, SENSITIVE_PATTERNS)
        or _looks_like(label, AUTH_PATTERNS)
    )
    legal = not voluntary and _looks_like(label, LEGAL_PATTERNS)
    confidence = 0.2 if control_type is ControlType.UNKNOWN else 0.9

    vault_keys = [name]
    if canonical and canonical != name:
        vault_keys.append(canonical)
    return FormControl(
        key=name or f"field_{position}",
        label=" ".join(label.split()),
        control_type=control_type,
        required=required,
        options=options,
        name=raw_name,
        input_type=raw_type,
        evidence=[f"greenhouse_api:{section}", f"type={raw_type}", f"name={raw_name}"],
        confidence=confidence,
        sensitive=sensitive,
        legal=legal,
        section=section,
        vault_keys=vault_keys,
        option_values=option_values,
    )


def _question_controls(questions: Iterable[dict[str, Any]], section: str, job_location: str, start: int) -> list[FormControl]:
    controls: list[FormControl] = []
    for question in questions or []:
        label = str(question.get("label") or "")
        fields = question.get("fields") or []
        for index, field_data in enumerate(fields):
            # Greenhouse groups alternatives under one question (e.g. a résumé
            # file OR pasted résumé text). Requiredness belongs to the primary
            # field; alternatives are optional.
            controls.append(_control_from_field(
                field_data,
                label=label,
                required=bool(question.get("required")) and index == 0,
                section=section,
                job_location=job_location,
                position=start + len(controls),
            ))
    return controls


def _demographic_controls(block: dict[str, Any] | None) -> list[FormControl]:
    controls: list[FormControl] = []
    for question in (block or {}).get("questions") or []:
        qid = str(question.get("id") or len(controls))
        label = " ".join(str(question.get("label") or "").split())
        raw_type = str(question.get("type") or "")
        control_type = _TYPE_MAP.get(raw_type, ControlType.UNKNOWN)
        options: list[str] = []
        option_values: dict[str, str] = {}
        for option in question.get("answer_options") or []:
            option_label = " ".join(str(option.get("label", "")).split())
            if option_label:
                options.append(option_label)
                option_values[option_label] = str(option.get("id", ""))
        key = f"demographic_{qid}"
        canonical = _demographic_key(label)
        controls.append(FormControl(
            key=key,
            label=label,
            control_type=control_type,
            required=bool(question.get("required")),
            options=options,
            name=qid,
            input_type=raw_type,
            evidence=["greenhouse_api:demographic", f"type={raw_type}", f"id={qid}"],
            confidence=0.2 if control_type is ControlType.UNKNOWN else 0.9,
            sensitive=True,
            legal=False,
            section="demographic",
            vault_keys=[key, canonical] if canonical else [key],
            option_values=option_values,
        ))
    return controls


_GDPR_CONSENTS = (
    ("requires_consent", "gdpr_consent_given", "Consent to the employer's candidate data processing notice"),
    ("requires_processing_consent", "gdpr_processing_consent_given", "Consent to processing of application data"),
    ("requires_retention_consent", "gdpr_retention_consent_given", "Consent to retention of application data"),
)


def _data_compliance_controls(entries: Iterable[dict[str, Any]] | None, has_demographics: bool) -> list[FormControl]:
    controls: list[FormControl] = []
    for entry in entries or []:
        wanted = [(name, label) for flag, name, label in _GDPR_CONSENTS if entry.get(flag)]
        if has_demographics and entry.get("demographic_data_consent_applies"):
            wanted.append(("gdpr_demographic_data_consent_given", "Consent to processing of voluntary demographic data"))
        for name, label in wanted:
            controls.append(FormControl(
                key=name,
                label=label,
                control_type=ControlType.CHECKBOX,
                required=True,
                name=name,
                input_type="checkbox",
                evidence=[f"greenhouse_api:data_compliance:{entry.get('type', '')}"],
                confidence=0.9,
                sensitive=False,
                legal=True,
                section="data_compliance",
                vault_keys=[name],
            ))
    return controls


def parse_greenhouse_payload(payload: dict[str, Any], ref: GreenhouseJobRef) -> RealForm:
    """Convert a ``?questions=true`` job payload into a :class:`RealForm`."""
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise FormFetchError("form_fetch_failed", "Greenhouse response did not include application questions")
    if not payload["questions"]:
        raise FormFetchError("form_fetch_failed", "Greenhouse returned an empty question list")

    job_location = str((payload.get("location") or {}).get("name") or "")
    controls = _question_controls(payload["questions"], "application", job_location, 0)
    controls += _question_controls(payload.get("location_questions") or [], "location", job_location, len(controls))
    for block in payload.get("compliance") or []:
        controls += _question_controls(block.get("questions") or [], "eeoc", job_location, len(controls))
    demographic = _demographic_controls(payload.get("demographic_questions"))
    controls += demographic
    controls += _data_compliance_controls(payload.get("data_compliance"), bool(demographic))

    seen: set[str] = set()
    for control in controls:
        if control.key in seen:
            raise FormFetchError("form_fetch_failed", f"duplicate field name {control.key!r} in Greenhouse payload")
        seen.add(control.key)

    warnings: list[str] = []
    deadline = payload.get("application_deadline")
    if deadline:
        warnings.append(f"application deadline: {deadline}")
    if any(control.control_type is ControlType.UNKNOWN for control in controls):
        warnings.append("form contains field types this engine does not support")

    return RealForm(
        platform="greenhouse",
        source="greenhouse_api",
        url=str(payload.get("absolute_url") or ref.embed_url),
        title=str(payload.get("title") or ""),
        controls=controls,
        vault_scopes=ref.vault_scopes,
        warnings=warnings,
        metadata={
            "board": ref.board,
            "job_id": ref.job_id,
            "api_url": ref.api_url,
            "embed_url": ref.embed_url,
            "job_location": job_location,
            "company_name": payload.get("company_name"),
            "updated_at": payload.get("updated_at"),
        },
    )


def fetch_greenhouse_payload(ref: GreenhouseJobRef, *, client: httpx.Client | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """GET the public job payload with questions. Never writes anything."""
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False, headers={"Accept": "application/json"})
    try:
        request = http.build_request("GET", ref.api_url, params={"questions": "true"})
        if request.method != "GET" or request.url.host != GREENHOUSE_API_HOST:
            raise FormFetchError("form_fetch_failed", "refusing non-GET or off-host form request")
        try:
            response = http.send(request)
        except httpx.HTTPError as exc:
            raise FormFetchError("form_fetch_failed", f"could not reach Greenhouse: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code == 404:
            raise FormFetchError("form_unavailable", "posting not found on the Greenhouse board (closed or removed)")
        if response.status_code != 200:
            raise FormFetchError("form_fetch_failed", f"Greenhouse returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise FormFetchError("form_fetch_failed", "Greenhouse returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise FormFetchError("form_fetch_failed", "Greenhouse returned an unexpected payload")
        return data
    finally:
        if owns_client:
            http.close()


def fetch_greenhouse_form(ref: GreenhouseJobRef, *, client: httpx.Client | None = None, timeout: float = 20.0) -> RealForm:
    return parse_greenhouse_payload(fetch_greenhouse_payload(ref, client=client, timeout=timeout), ref)
