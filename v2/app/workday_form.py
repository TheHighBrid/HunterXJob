"""Read-only extraction and modeling of Workday application forms.

Workday serves hosted job postings and application forms at
``https://{tenant}.wd{n}.myworkdayjobs.com/en-US/{company}/job/...`` or
``https://{tenant}.myworkdayjobs.com/...``.

Standard fields include legal name (first/last), email, phone, location/address,
resume attachment, cover letter, work authorization questions, and terms agreement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from app.form_engine import AUTH_PATTERNS, LEGAL_PATTERNS, ControlType, FormControl
from app.greenhouse_form import (
    FormFetchError,
    RealForm,
)

WORKDAY_DOMAINS = ("myworkdayjobs.com",)
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


@dataclass(frozen=True, slots=True)
class WorkdayJobRef:
    tenant: str
    company: str
    posting_id: str
    url: str = ""

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.tenant or ""):
            raise ValueError(f"invalid Workday tenant: {self.tenant!r}")
        if not _ID_RE.match(self.posting_id or ""):
            raise ValueError(f"invalid Workday posting id: {self.posting_id!r}")

    @property
    def page_url(self) -> str:
        if self.url:
            return self.url
        return f"https://{self.tenant}.myworkdayjobs.com/en-US/{self.company}/job/{self.posting_id}"

    @property
    def apply_url(self) -> str:
        return f"{self.page_url}/apply" if not self.page_url.endswith("/apply") else self.page_url

    @property
    def vault_scopes(self) -> list[str]:
        t = self.tenant.lower()
        return [f"workday:{t}", f"workday:{t}:{self.posting_id}"]


def parse_workday_ref(url: str) -> WorkdayJobRef | None:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not any(domain in host for domain in WORKDAY_DOMAINS):
        return None
    # Tenant from host: e.g. company.wd3.myworkdayjobs.com -> tenant is company
    host_parts = host.split(".")
    tenant = host_parts[0]
    
    # Path parsing: /en-US/{company}/job/{location}/{title}_{posting_id}
    path_parts = [p for p in parsed.path.split("/") if p and p.lower() != "apply"]
    company = tenant
    for part in path_parts:
        if part.lower() not in ("en-us", "job"):
            company = part
            break

    posting_id = ""
    if path_parts:
        last = path_parts[-1]
        if "_" in last:
            posting_id = last.split("_")[-1]
        else:
            posting_id = last

    if not posting_id:
        posting_id = "job"

    try:
        return WorkdayJobRef(tenant=tenant, company=company, posting_id=posting_id, url=url)
    except ValueError:
        return None


def workday_ref_for_job(job: Any) -> WorkdayJobRef | None:
    ref = parse_workday_ref(getattr(job, "url", "") or "")
    if ref is not None:
        return ref
    source = (getattr(job, "source", "") or "").lower()
    if source == "workday":
        tenant = getattr(job, "board", None) or getattr(job, "company", None) or "company"
        pid = getattr(job, "external_id", None) or "job"
        try:
            return WorkdayJobRef(tenant=tenant, company=tenant, posting_id=pid, url=getattr(job, "url", "") or "")
        except ValueError:
            return None
    return None


class _WorkdayHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.controls: list[FormControl] = []
        self._current_tag: str | None = None
        self._current_label = ""
        self._collect_label = False
        self._options: list[str] = []
        self._in_select = False
        self._select_attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        self._current_tag = tag

        if tag == "label":
            self._collect_label = True
            self._current_label = ""
        elif tag == "select":
            self._in_select = True
            self._select_attrs = ad
            self._options = []
        elif tag in ("input", "textarea"):
            data_auto = ad.get("data-automation-id", "")
            name = ad.get("name") or ad.get("id") or data_auto
            input_type = ad.get("type", "text" if tag == "input" else "textarea").lower()
            required = "required" in ad or ad.get("aria-required") == "true"
            label = ad.get("aria-label") or ad.get("placeholder") or self._current_label or data_auto or name

            ctrl_type = ControlType.TEXT
            if input_type == "email":
                ctrl_type = ControlType.EMAIL
            elif input_type in ("tel", "phone"):
                ctrl_type = ControlType.TEL
            elif input_type == "file":
                ctrl_type = ControlType.FILE
            elif input_type == "checkbox":
                ctrl_type = ControlType.CHECKBOX
            elif input_type == "radio":
                ctrl_type = ControlType.RADIO
            elif tag == "textarea":
                ctrl_type = ControlType.TEXTAREA

            key = _map_workday_key(data_auto, name, label, ctrl_type)
            is_legal = any(p in label.lower() for p in LEGAL_PATTERNS) or "agreement" in data_auto
            is_auth = any(p in label.lower() for p in AUTH_PATTERNS) or "workauth" in data_auto.lower()

            if key and not any(c.key == key for c in self.controls):
                self.controls.append(
                    FormControl(
                        key=key,
                        label=label.strip(),
                        control_type=ctrl_type,
                        required=required,
                        name=name,
                        input_type=input_type,
                        legal=is_legal,
                        section="authorization" if is_auth else ("legal" if is_legal else "application"),
                        confidence=0.9 if key in ("first_name", "last_name", "email", "phone", "resume") else 0.7,
                    )
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self._collect_label = False
        elif tag == "select":
            self._in_select = False
            data_auto = self._select_attrs.get("data-automation-id", "")
            name = self._select_attrs.get("name") or data_auto
            label = self._select_attrs.get("aria-label") or self._current_label or name
            required = "required" in self._select_attrs or self._select_attrs.get("aria-required") == "true"
            key = _map_workday_key(data_auto, name, label, ControlType.SELECT)
            if key and not any(c.key == key for c in self.controls):
                self.controls.append(
                    FormControl(
                        key=key,
                        label=label.strip(),
                        control_type=ControlType.SELECT,
                        required=required,
                        name=name,
                        options=list(self._options),
                    )
                )

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._collect_label:
            self._current_label = f"{self._current_label} {text}".strip()
        if self._in_select and self._current_tag == "option":
            self._options.append(text)


def _map_workday_key(data_auto: str, name: str, label: str, ctype: ControlType) -> str:
    hay = f"{data_auto} {name} {label}".lower()
    if ctype == ControlType.FILE or "file-upload" in hay or "resume" in hay:
        return "resume"
    if "cover-letter" in hay or "coverletter" in hay:
        return "cover_letter"
    if "firstname" in hay or "legalnamesection_firstname" in hay:
        return "first_name"
    if "lastname" in hay or "legalnamesection_lastname" in hay:
        return "last_name"
    if "email" in hay:
        return "email"
    if "phone" in hay:
        return "phone"
    if "addressline1" in hay or "address_line" in hay:
        return "address_line1"
    if "city" in hay:
        return "city"
    if "postalcode" in hay or "postal_code" in hay or "zip" in hay:
        return "postal_code"
    if "country" in hay:
        return "country"
    if "authorized" in hay or "workauth_authorized" in hay or "legally authorized" in hay:
        return "authorized_to_work"
    if "sponsorship" in hay or "workauth_sponsorship" in hay or "require sponsorship" in hay:
        return "require_sponsorship"
    if "agreement" in hay or "terms" in hay or "privacy" in hay or any(p in hay for p in LEGAL_PATTERNS):
        return "terms_agreement"
    slug = re.sub(r"[^a-z0-9]+", "_", hay).strip("_")
    return slug[:50] or "custom_field"


def default_workday_controls() -> list[FormControl]:
    """Standard controls present on Workday application forms."""
    return [
        FormControl(key="first_name", label="Legal First Name", control_type=ControlType.TEXT, required=True, name="legalNameSection_firstName"),
        FormControl(key="last_name", label="Legal Last Name", control_type=ControlType.TEXT, required=True, name="legalNameSection_lastName"),
        FormControl(key="email", label="Email Address", control_type=ControlType.EMAIL, required=True, name="email"),
        FormControl(key="phone", label="Phone Number", control_type=ControlType.TEL, required=False, name="phoneNumber"),
        FormControl(key="city", label="City", control_type=ControlType.TEXT, required=False, name="addressSection_city"),
        FormControl(key="resume", label="Resume / CV", control_type=ControlType.FILE, required=True, name="resumeUpload"),
        FormControl(key="cover_letter", label="Cover Letter", control_type=ControlType.FILE, required=False, name="coverLetterUpload"),
        FormControl(key="authorized_to_work", label="Are you legally authorized to work in Canada?", control_type=ControlType.SELECT, required=True, options=["Yes", "No"], name="workAuth_authorized"),
        FormControl(key="require_sponsorship", label="Will you now or in the future require visa sponsorship?", control_type=ControlType.SELECT, required=True, options=["Yes", "No"], name="workAuth_sponsorship"),
        FormControl(key="terms_agreement", label="I acknowledge and agree to the terms", control_type=ControlType.CHECKBOX, required=True, name="agreementCheckbox", legal=True, section="legal"),
    ]


def parse_workday_form(html: str, ref: WorkdayJobRef, title: str = "") -> RealForm:
    parser = _WorkdayHTMLParser()
    parser.feed(html)
    controls = parser.controls if parser.controls else default_workday_controls()
    return RealForm(
        platform="workday",
        source="workday",
        url=ref.apply_url,
        title=title or f"{ref.company} - {ref.posting_id}",
        controls=controls,
        vault_scopes=ref.vault_scopes,
    )


def fetch_workday_form(
    ref: WorkdayJobRef,
    *,
    job_location: str = "",
    client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> RealForm:
    """Fetch and parse Workday application form."""
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = http.get(ref.apply_url)
        if resp.status_code in (404, 410):
            raise FormFetchError("form_unavailable", f"posting not found: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise FormFetchError("form_fetch_failed", f"HTTP {resp.status_code} from Workday")
        return parse_workday_form(resp.text, ref)
    except httpx.TransportError as exc:
        raise FormFetchError("form_fetch_failed", f"network error: {exc}") from exc
