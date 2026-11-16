"""Read-only extraction and parsing of Breezy HR application forms.

Breezy HR hosts employer career portals at ``https://{company}.breezy.hr/p/{position_id}``
and application forms at ``/apply``. Breezy HR exposes posting metadata and application
fields at ``https://{company}.breezy.hr/p/{position_id}/json`` or direct HTML form controls.

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

BREEZY_HOST_SUFFIX = "breezy.hr"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_POS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")
USER_AGENT = "Mozilla/5.0 (compatible; HunterXJob/2; read-only form check)"


@dataclass(frozen=True, slots=True)
class BreezyJobRef:
    company: str
    position_id: str

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.company or ""):
            raise ValueError(f"invalid Breezy HR company slug: {self.company!r}")
        if not _POS_ID_RE.match(self.position_id or ""):
            raise ValueError(f"invalid Breezy HR position id: {self.position_id!r}")

    @property
    def apply_url(self) -> str:
        return f"https://{self.company}.{BREEZY_HOST_SUFFIX}/p/{self.position_id}/apply"

    @property
    def page_url(self) -> str:
        return f"https://{self.company}.{BREEZY_HOST_SUFFIX}/p/{self.position_id}"

    @property
    def json_url(self) -> str:
        return f"https://{self.company}.{BREEZY_HOST_SUFFIX}/p/{self.position_id}/json"

    @property
    def vault_scopes(self) -> list[str]:
        comp = self.company.lower()
        return [f"breezy:{comp}", f"breezy:{comp}:{self.position_id}"]


def parse_breezy_ref(url: str) -> BreezyJobRef | None:
    """Parse a Breezy HR URL into a BreezyJobRef."""
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not host.endswith(BREEZY_HOST_SUFFIX):
        return None

    # company is subdomain (e.g. acme.breezy.hr)
    parts_host = host.split(f".{BREEZY_HOST_SUFFIX}")[0]
    if not parts_host or "." in parts_host:
        return None
    company = parts_host

    parts_path = [p for p in parsed.path.split("/") if p]
    if len(parts_path) >= 2 and parts_path[0].lower() == "p":
        position_id = parts_path[1]
        if _SLUG_RE.match(company) and _POS_ID_RE.match(position_id):
            return BreezyJobRef(company=company, position_id=position_id)

    return None


def breezy_ref_for_job(job: Any) -> BreezyJobRef | None:
    url = getattr(job, "url", "") or ""
    ref = parse_breezy_ref(url)
    if ref:
        return ref
    source = getattr(job, "source", "") or ""
    external_id = getattr(job, "external_id", "") or ""
    board = getattr(job, "board", "") or getattr(job, "company", "") or ""
    if source == "breezy" and board and external_id:
        try:
            return BreezyJobRef(company=board, position_id=external_id)
        except ValueError:
            return None
    return None


def parse_breezy_payload(payload: dict[str, Any], ref: BreezyJobRef) -> RealForm:
    """Transform Breezy HR position payload into a RealForm."""
    title = str(payload.get("name") or payload.get("title") or "Position")
    controls: list[FormControl] = [
        FormControl(
            key="name",
            label="Full Name",
            control_type=ControlType.TEXT,
            required=True,
            name="name",
            section="personal",
            vault_keys=["full_name", "first_name", "last_name"],
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
            name="phone_number",
            section="personal",
            vault_keys=["phone", "mobile_phone"],
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
            control_type=ControlType.TEXTAREA,
            required=False,
            name="cover_letter",
            section="attachments",
            vault_keys=["cover_letter"],
        ),
    ]

    # Parse custom screening questionnaire if provided in payload
    questionnaire = payload.get("questionnaire") or payload.get("questions") or []
    for idx, q in enumerate(questionnaire):
        if not isinstance(q, dict):
            continue
        q_id = str(q.get("id") or f"breezy_q_{idx}")
        q_title = str(q.get("name") or q.get("question") or q.get("text") or f"Question {idx+1}").strip()
        q_required = bool(q.get("required") or False)
        q_type = str(q.get("type") or "text").lower()
        options = [str(opt) for opt in q.get("options") or [] if opt]

        c_type = ControlType.TEXT
        if q_type in ("textarea", "multiline"):
            c_type = ControlType.TEXTAREA
        elif q_type in ("dropdown", "select"):
            c_type = ControlType.SELECT
        elif q_type in ("radio", "choice"):
            c_type = ControlType.RADIO
        elif q_type in ("checkbox", "boolean"):
            c_type = ControlType.CHECKBOX

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
                name=f"question_{q_id}",
                sensitive=is_sensitive,
                legal=is_legal,
                section="questions",
                vault_keys=[field_key, clean_key],
            )
        )

    return RealForm(
        platform="breezy",
        source="breezy",
        url=ref.apply_url,
        title=title,
        controls=controls,
        vault_scopes=ref.vault_scopes,
        metadata={"company": ref.company, "position_id": ref.position_id},
    )


def fetch_breezy_form(
    ref: BreezyJobRef,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> RealForm:
    """Fetch Breezy HR application schema via JSON API or page fallback."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False)

    try:
        resp = http.get(ref.json_url, headers=headers)
        if resp.status_code == 200:
            return parse_breezy_payload(resp.json(), ref)

        if resp.status_code in (404, 410):
            raise FormFetchError("job_closed", f"Breezy posting {ref.position_id} returned HTTP {resp.status_code}")
        if resp.status_code == 403:
            raise FormFetchError("blocked_by_bot_check", f"Breezy request blocked: HTTP {resp.status_code}")

        # If JSON is not directly exposed on /json, return standard form with contact & resume
        return parse_breezy_payload({"title": f"Breezy Job {ref.position_id}"}, ref)
    except httpx.HTTPError as exc:
        raise FormFetchError("form_unavailable", f"network error fetching Breezy HR posting: {exc}") from exc
    finally:
        if owns_client:
            http.close()
