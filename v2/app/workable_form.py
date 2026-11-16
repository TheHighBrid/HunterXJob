"""Read-only extraction and parsing of Workable application forms.

Workable hosts postings at ``https://apply.workable.com/{company}/j/{shortcode}/``
and the application flow at ``/apply``. Workable's public API at
``https://apply.workable.com/api/v3/accounts/{company}/jobs/{shortcode}`` returns
the job description, location, and the full schema of standard and custom application
questions.

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

WORKABLE_HOST = "apply.workable.com"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
USER_AGENT = "Mozilla/5.0 (compatible; HunterXJob/2; read-only form check)"


@dataclass(frozen=True, slots=True)
class WorkableJobRef:
    company: str
    shortcode: str

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.company or ""):
            raise ValueError(f"invalid Workable company slug: {self.company!r}")
        if not _SHORTCODE_RE.match(self.shortcode or ""):
            raise ValueError(f"invalid Workable shortcode: {self.shortcode!r}")

    @property
    def apply_url(self) -> str:
        return f"https://{WORKABLE_HOST}/{self.company}/j/{self.shortcode}/apply"

    @property
    def page_url(self) -> str:
        return f"https://{WORKABLE_HOST}/{self.company}/j/{self.shortcode}/"

    @property
    def api_url(self) -> str:
        return f"https://{WORKABLE_HOST}/api/v3/accounts/{self.company}/jobs/{self.shortcode}"

    @property
    def vault_scopes(self) -> list[str]:
        comp = self.company.lower()
        return [f"workable:{comp}", f"workable:{comp}:{self.shortcode}"]


def parse_workable_ref(url: str) -> WorkableJobRef | None:
    """Parse a Workable URL into a WorkableJobRef."""
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if host != WORKABLE_HOST and not host.endswith(f".{WORKABLE_HOST}") and "workable.com" not in host:
        return None

    # Path pattern: /{company}/j/{shortcode}/...
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 3 and parts[1].lower() == "j":
        company, shortcode = parts[0], parts[2]
        if _SLUG_RE.match(company) and _SHORTCODE_RE.match(shortcode):
            return WorkableJobRef(company=company, shortcode=shortcode)

    # Subdomain pattern: {company}.workable.com/j/{shortcode}
    if host.endswith(".workable.com"):
        subdomain = host.split(".workable.com")[0]
        if len(parts) >= 2 and parts[0].lower() == "j" and _SLUG_RE.match(subdomain):
            shortcode = parts[1]
            if _SHORTCODE_RE.match(shortcode):
                return WorkableJobRef(company=subdomain, shortcode=shortcode)

    return None


def workable_ref_for_job(job: Any) -> WorkableJobRef | None:
    url = getattr(job, "url", "") or ""
    ref = parse_workable_ref(url)
    if ref:
        return ref
    source = getattr(job, "source", "") or ""
    external_id = getattr(job, "external_id", "") or ""
    board = getattr(job, "board", "") or getattr(job, "company", "") or ""
    if source == "workable" and board and external_id:
        try:
            return WorkableJobRef(company=board, shortcode=external_id)
        except ValueError:
            return None
    return None


def _classify_question_type(q_type: str) -> ControlType:
    t = (q_type or "").lower()
    if t in ("free_text", "string", "text", "short_text"):
        return ControlType.TEXT
    if t in ("paragraph", "long_text", "textarea"):
        return ControlType.TEXTAREA
    if t in ("choice", "dropdown", "select"):
        return ControlType.SELECT
    if t in ("multiple_choice", "multi_choice", "radio"):
        return ControlType.RADIO
    if t in ("boolean", "yes_no", "checkbox"):
        return ControlType.CHECKBOX
    if t in ("number", "numeric", "integer"):
        return ControlType.NUMBER
    if t in ("date",):
        return ControlType.DATE
    if t in ("file", "attachment"):
        return ControlType.FILE
    return ControlType.TEXT


def parse_workable_payload(payload: dict[str, Any], ref: WorkableJobRef) -> RealForm:
    """Transform Workable API response into a RealForm."""
    title = str(payload.get("title") or payload.get("name") or "Position")
    controls: list[FormControl] = [
        # Standard contact fields
        FormControl(
            key="first_name",
            label="First Name",
            control_type=ControlType.TEXT,
            required=True,
            name="firstname",
            section="personal",
            vault_keys=["first_name", "full_name"],
        ),
        FormControl(
            key="last_name",
            label="Last Name",
            control_type=ControlType.TEXT,
            required=True,
            name="lastname",
            section="personal",
            vault_keys=["last_name", "full_name"],
        ),
        FormControl(
            key="email",
            label="Email Address",
            control_type=ControlType.EMAIL,
            required=True,
            name="email",
            section="personal",
            vault_keys=["email"],
        ),
        FormControl(
            key="phone",
            label="Phone Number",
            control_type=ControlType.TEL,
            required=False,
            name="phone",
            section="personal",
            vault_keys=["phone", "mobile_phone"],
        ),
        FormControl(
            key="location",
            label="Address / Location",
            control_type=ControlType.AUTOCOMPLETE,
            required=False,
            name="address",
            section="personal",
            vault_keys=["location", "city", "address"],
        ),
        FormControl(
            key="resume",
            label="Resume / CV",
            control_type=ControlType.FILE,
            required=True,
            name="resume",
            section="attachments",
            vault_keys=["resume"],
        ),
        FormControl(
            key="cover_letter",
            label="Cover Letter",
            control_type=ControlType.FILE,
            required=False,
            name="cover_letter",
            section="attachments",
            vault_keys=["cover_letter"],
        ),
    ]

    # Parse custom screening questions if present in payload
    questions = payload.get("questions") or payload.get("application_questions") or []
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        q_id = str(q.get("id") or f"question_{idx}")
        q_title = str(q.get("title") or q.get("body") or f"Question {idx+1}").strip()
        q_type_str = str(q.get("type") or "free_text")
        q_required = bool(q.get("required") or False)
        raw_choices = q.get("choices") or q.get("options") or []
        options = [str(c.get("label") if isinstance(c, dict) else c) for c in raw_choices if c]

        c_type = _classify_question_type(q_type_str)
        lower_title = q_title.lower()
        is_sensitive = any(pat in lower_title for pat in SENSITIVE_PATTERNS)
        is_legal = any(pat in lower_title for pat in LEGAL_PATTERNS) or any(pat in lower_title for pat in AUTH_PATTERNS)

        clean_key = re.sub(r"[^a-z0-9_]+", "_", lower_title.strip()).strip("_")[:50]
        field_key = f"q_{clean_key}" if clean_key else f"q_{q_id}"

        controls.append(
            FormControl(
                key=field_key,
                label=q_title,
                control_type=c_type,
                required=q_required,
                options=options,
                name=f"questions[{q_id}]",
                sensitive=is_sensitive,
                legal=is_legal,
                section="questions",
                vault_keys=[field_key, clean_key],
            )
        )

    return RealForm(
        platform="workable",
        source="workable",
        url=ref.apply_url,
        title=title,
        controls=controls,
        vault_scopes=ref.vault_scopes,
        metadata={"company": ref.company, "shortcode": ref.shortcode},
    )


def fetch_workable_form(
    ref: WorkableJobRef,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> RealForm:
    """Fetch Workable job application schema via public read-only API or page."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False)

    try:
        # 1. Try public v3 API
        resp = http.get(ref.api_url, headers=headers)
        if resp.status_code == 200:
            return parse_workable_payload(resp.json(), ref)

        # 2. Fallback to v1 widget endpoint
        widget_url = f"https://{WORKABLE_HOST}/api/v1/widget/accounts/{ref.company}/jobs/{ref.shortcode}"
        resp_widget = http.get(widget_url, headers=headers)
        if resp_widget.status_code == 200:
            return parse_workable_payload(resp_widget.json(), ref)

        if resp.status_code in (404, 410):
            raise FormFetchError("job_closed", f"Workable posting {ref.shortcode} returned HTTP {resp.status_code}")
        if resp.status_code == 403:
            raise FormFetchError("blocked_by_bot_check", f"Workable request blocked: HTTP {resp.status_code}")

        raise FormFetchError("form_unavailable", f"Workable returned HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        raise FormFetchError("form_unavailable", f"network error fetching Workable posting: {exc}") from exc
    finally:
        if owns_client:
            http.close()
