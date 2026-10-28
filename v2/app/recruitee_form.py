"""Read-only extraction and parsing of Recruitee application forms.

Recruitee hosts employer career portals at ``https://{company}.recruitee.com/o/{slug}``
and application forms at ``/apply``. Recruitee exposes public job offer metadata
and screening questions via ``https://{company}.recruitee.com/api/offers/{slug}``.

Safety contract:
* Only read-only HTTP GET requests; redirects are not followed.
* Nothing is submitted, uploaded or posted.
* Failures raise :class:`FormFetchError`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.form_engine import (
    AUTH_PATTERNS,
    LEGAL_PATTERNS,
    SENSITIVE_PATTERNS,
    ControlType,
    FormControl,
)
from app.greenhouse_form import FormFetchError, RealForm

RECRUITEE_HOST_SUFFIX = "recruitee.com"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_OFFER_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
USER_AGENT = "Mozilla/5.0 (compatible; HunterXJob/2; read-only form check)"


@dataclass(frozen=True, slots=True)
class RecruiteeJobRef:
    company: str
    slug: str

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.company or ""):
            raise ValueError(f"invalid Recruitee company slug: {self.company!r}")
        if not _OFFER_SLUG_RE.match(self.slug or ""):
            raise ValueError(f"invalid Recruitee offer slug: {self.slug!r}")

    @property
    def apply_url(self) -> str:
        return f"https://{self.company}.{RECRUITEE_HOST_SUFFIX}/o/{self.slug}/apply"

    @property
    def page_url(self) -> str:
        return f"https://{self.company}.{RECRUITEE_HOST_SUFFIX}/o/{self.slug}"

    @property
    def api_url(self) -> str:
        return f"https://{self.company}.{RECRUITEE_HOST_SUFFIX}/api/offers/{self.slug}"

    @property
    def vault_scopes(self) -> list[str]:
        comp = self.company.lower()
        return [f"recruitee:{comp}", f"recruitee:{comp}:{self.slug}"]


def parse_recruitee_ref(url: str) -> RecruiteeJobRef | None:
    """Parse a Recruitee URL into a RecruiteeJobRef."""
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not host.endswith(RECRUITEE_HOST_SUFFIX):
        return None

    company = host.split(f".{RECRUITEE_HOST_SUFFIX}")[0]
    if not company or "." in company:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "o":
        slug = parts[1]
        if _SLUG_RE.match(company) and _OFFER_SLUG_RE.match(slug):
            return RecruiteeJobRef(company=company, slug=slug)

    return None


def recruitee_ref_for_job(job: Any) -> RecruiteeJobRef | None:
    url = getattr(job, "url", "") or ""
    ref = parse_recruitee_ref(url)
    if ref:
        return ref
    source = getattr(job, "source", "") or ""
    external_id = getattr(job, "external_id", "") or ""
    board = getattr(job, "board", "") or getattr(job, "company", "") or ""
    if source == "recruitee" and board and external_id:
        try:
            return RecruiteeJobRef(company=board, slug=external_id)
        except ValueError:
            return None
    return None


def parse_recruitee_payload(payload: dict[str, Any], ref: RecruiteeJobRef) -> RealForm:
    """Transform Recruitee API offer payload into a RealForm."""
    offer = payload.get("offer") or payload
    title = str(offer.get("title") or "Position")
    controls: list[FormControl] = [
        FormControl(
            key="name",
            label="Full Name",
            control_type=ControlType.TEXT,
            required=True,
            name="candidate[name]",
            section="personal",
            vault_keys=["full_name", "first_name", "last_name"],
        ),
        FormControl(
            key="email",
            label="Email Address",
            control_type=ControlType.EMAIL,
            required=True,
            name="candidate[email]",
            section="personal",
            vault_keys=["email"],
        ),
        FormControl(
            key="phone",
            label="Phone Number",
            control_type=ControlType.TEL,
            required=False,
            name="candidate[phone]",
            section="personal",
            vault_keys=["phone", "mobile_phone"],
        ),
        FormControl(
            key="resume",
            label="Resume / CV",
            control_type=ControlType.FILE,
            required=True,
            name="candidate[cv]",
            section="attachments",
            vault_keys=["resume"],
        ),
        FormControl(
            key="cover_letter",
            label="Cover Letter",
            control_type=ControlType.TEXTAREA,
            required=False,
            name="candidate[cover_letter]",
            section="attachments",
            vault_keys=["cover_letter"],
        ),
    ]

    # Parse open questions
    questions = offer.get("questions") or []
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        q_id = str(q.get("id") or f"recruitee_q_{idx}")
        q_body = str(q.get("body") or q.get("title") or f"Question {idx+1}").strip()
        q_kind = str(q.get("kind") or "text").lower()
        q_required = bool(q.get("required") or False)
        raw_choices = q.get("choices") or q.get("options") or []
        options = [str(c.get("body") if isinstance(c, dict) else c) for c in raw_choices if c]

        c_type = ControlType.TEXT
        if q_kind in ("textarea", "multiline"):
            c_type = ControlType.TEXTAREA
        elif q_kind in ("dropdown", "single_choice"):
            c_type = ControlType.SELECT
        elif q_kind in ("multiple_choice", "checkbox"):
            c_type = ControlType.CHECKBOX
        elif q_kind in ("boolean", "yes_no"):
            c_type = ControlType.CHECKBOX

        lower_body = q_body.lower()
        is_sensitive = any(pat in lower_body for pat in SENSITIVE_PATTERNS)
        is_legal = any(pat in lower_body for pat in LEGAL_PATTERNS) or any(pat in lower_body for pat in AUTH_PATTERNS)
        clean_key = re.sub(r"[^a-z0-9_]+", "_", lower_body.strip()).strip("_")[:50]
        field_key = f"q_{clean_key}" if clean_key else f"q_{q_id}"

        controls.append(
            FormControl(
                key=field_key,
                label=q_body,
                control_type=c_type,
                required=q_required,
                options=options,
                name=f"candidate[open_questions_attributes][{idx}][value]",
                sensitive=is_sensitive,
                legal=is_legal,
                section="questions",
                vault_keys=[field_key, clean_key],
            )
        )

    return RealForm(
        platform="recruitee",
        source="recruitee",
        url=ref.apply_url,
        title=title,
        controls=controls,
        vault_scopes=ref.vault_scopes,
        metadata={"company": ref.company, "slug": ref.slug},
    )


def fetch_recruitee_form(
    ref: RecruiteeJobRef,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> RealForm:
    """Fetch Recruitee application schema via public offers API."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False)

    try:
        resp = http.get(ref.api_url, headers=headers)
        if resp.status_code == 200:
            return parse_recruitee_payload(resp.json(), ref)

        if resp.status_code in (404, 410):
            raise FormFetchError("job_closed", f"Recruitee offer {ref.slug} returned HTTP {resp.status_code}")
        if resp.status_code == 403:
            raise FormFetchError("blocked_by_bot_check", f"Recruitee request blocked: HTTP {resp.status_code}")

        return parse_recruitee_payload({"title": f"Recruitee Job {ref.slug}"}, ref)
    except httpx.HTTPError as exc:
        raise FormFetchError("form_unavailable", f"network error fetching Recruitee posting: {exc}") from exc
    finally:
        if owns_client:
            http.close()
