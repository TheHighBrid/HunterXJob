"""Read-only extraction and modeling of SmartRecruiters application forms.

SmartRecruiters serves application forms at
``https://jobs.smartrecruiters.com/{company}/{posting_id}`` or
``https://jobs.smartrecruiters.com/{company}/{posting_id}/apply``.

Standard fields include first name, last name, email, phone, location,
resume file input, and additional information (cover letter/message), plus
employer-specific custom screening questions and privacy consent checkboxes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from app.form_engine import LEGAL_PATTERNS, ControlType, FormControl
from app.greenhouse_form import (
    FormFetchError,
    RealForm,
    demographic_key,
    is_sensitive_field,
    looks_like,
    shared_answer_key,
)

SMARTRECRUITERS_HOSTS = ("jobs.smartrecruiters.com", "careers.smartrecruiters.com", "www.smartrecruiters.com")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


@dataclass(frozen=True, slots=True)
class SmartRecruitersJobRef:
    company: str
    posting_id: str

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.company or ""):
            raise ValueError(f"invalid SmartRecruiters company slug: {self.company!r}")
        if not _ID_RE.match(self.posting_id or ""):
            raise ValueError(f"invalid SmartRecruiters posting id: {self.posting_id!r}")

    @property
    def page_url(self) -> str:
        return f"https://jobs.smartrecruiters.com/{self.company}/{self.posting_id}"

    @property
    def apply_url(self) -> str:
        return f"https://jobs.smartrecruiters.com/{self.company}/{self.posting_id}/apply"

    @property
    def vault_scopes(self) -> list[str]:
        co = self.company.lower()
        return [f"smartrecruiters:{co}", f"smartrecruiters:{co}:{self.posting_id}"]


def parse_smartrecruiters_ref(url: str) -> SmartRecruitersJobRef | None:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not any(host == h or host.endswith(f".{h}") for h in ("smartrecruiters.com",)):
        return None
    parts = [p for p in parsed.path.split("/") if p and p != "apply"]
    if len(parts) >= 2:
        company, posting_id = parts[0], parts[1]
        if _SLUG_RE.match(company) and _ID_RE.match(posting_id):
            return SmartRecruitersJobRef(company, posting_id)
    return None


def smartrecruiters_ref_for_job(job: Any) -> SmartRecruitersJobRef | None:
    ref = parse_smartrecruiters_ref(getattr(job, "url", "") or "")
    if ref is not None:
        return ref
    source = (getattr(job, "source", "") or "").lower()
    if source == "smartrecruiters":
        co = getattr(job, "board", None) or getattr(job, "company", None) or ""
        pid = getattr(job, "external_id", None) or ""
        if _SLUG_RE.match(co) and _ID_RE.match(pid):
            return SmartRecruitersJobRef(co, pid)
    return None


class _SmartRecruitersHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.controls: list[FormControl] = []
        self._current_tag: str | None = None
        self._current_label = ""
        self._collect_label = False
        self._options: list[str] = []
        self._option_values: dict[str, str] = {}
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
            self._option_values = {}
        elif tag == "option" and self._in_select:
            pass  # wait for text
        elif tag in ("input", "textarea"):
            name = ad.get("name") or ad.get("id") or ""
            input_type = ad.get("type", "text" if tag == "input" else "textarea").lower()
            required = "required" in ad or ad.get("aria-required") == "true"
            label = ad.get("placeholder") or ad.get("aria-label") or self._current_label or name
            
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

            key = _map_key(name, label, ctrl_type)
            is_legal = any(pat in label.lower() for pat in LEGAL_PATTERNS) or "consent" in key or "privacy" in key

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
                        section="legal" if is_legal else "application",
                        confidence=0.9 if key in ("first_name", "last_name", "email", "phone", "resume") else 0.7,
                    )
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self._collect_label = False
        elif tag == "select":
            self._in_select = False
            name = self._select_attrs.get("name") or self._select_attrs.get("id") or ""
            label = self._select_attrs.get("aria-label") or self._current_label or name
            required = "required" in self._select_attrs or self._select_attrs.get("aria-required") == "true"
            key = _map_key(name, label, ControlType.SELECT)
            is_legal = any(pat in label.lower() for pat in LEGAL_PATTERNS)
            if key and not any(c.key == key for c in self.controls):
                self.controls.append(
                    FormControl(
                        key=key,
                        label=label.strip(),
                        control_type=ControlType.SELECT,
                        required=required,
                        name=name,
                        options=list(self._options),
                        option_values=dict(self._option_values),
                        legal=is_legal,
                        section="legal" if is_legal else "application",
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
            self._option_values[text] = text


def _map_key(name: str, label: str, ctype: ControlType) -> str:
    hay = f"{name} {label}".lower()
    if ctype == ControlType.FILE or "resume" in hay or "cv" in hay:
        return "resume"
    if "first" in hay and "name" in hay or name == "firstName":
        return "first_name"
    if "last" in hay and "name" in hay or name == "lastName":
        return "last_name"
    if "email" in hay:
        return "email"
    if "phone" in hay or "mobile" in hay or name == "phoneNumber":
        return "phone"
    if "cover" in hay and "letter" in hay or "additionalinformation" in hay or "message" in hay:
        return "cover_letter"
    if "linkedin" in hay:
        return "linkedin"
    if "website" in hay or "portfolio" in hay:
        return "website"
    if "city" in hay or "location" in hay:
        return "location"
    if any(pat in hay for pat in LEGAL_PATTERNS) or "consent" in hay or "privacy" in hay:
        return "terms_consent"
    slug = re.sub(r"[^a-z0-9]+", "_", hay).strip("_")
    return slug[:50] or "custom_field"


def default_smartrecruiters_controls() -> list[FormControl]:
    """Standard controls present on SmartRecruiters application forms."""
    return [
        FormControl(key="first_name", label="First Name", control_type=ControlType.TEXT, required=True, name="firstName"),
        FormControl(key="last_name", label="Last Name", control_type=ControlType.TEXT, required=True, name="lastName"),
        FormControl(key="email", label="Email", control_type=ControlType.EMAIL, required=True, name="email"),
        FormControl(key="phone", label="Phone number", control_type=ControlType.TEL, required=False, name="phoneNumber"),
        FormControl(key="location", label="Location", control_type=ControlType.TEXT, required=False, name="location"),
        FormControl(key="resume", label="Resume / CV", control_type=ControlType.FILE, required=True, name="file"),
        FormControl(key="cover_letter", label="Message to Hiring Manager", control_type=ControlType.TEXTAREA, required=False, name="additionalInformation"),
        FormControl(key="terms_consent", label="Privacy Policy & Terms", control_type=ControlType.CHECKBOX, required=True, name="privacyPolicy", legal=True, section="legal"),
    ]


def parse_smartrecruiters_form(html: str, ref: SmartRecruitersJobRef, title: str = "") -> RealForm:
    parser = _SmartRecruitersHTMLParser()
    parser.feed(html)
    controls = parser.controls if parser.controls else default_smartrecruiters_controls()
    return RealForm(
        platform="smartrecruiters",
        source="smartrecruiters",
        url=ref.apply_url,
        title=title or f"{ref.company} - {ref.posting_id}",
        controls=controls,
        vault_scopes=ref.vault_scopes,
    )


def fetch_smartrecruiters_form(
    ref: SmartRecruitersJobRef,
    *,
    job_location: str = "",
    client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> RealForm:
    """Fetch and parse SmartRecruiters application form."""
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = http.get(ref.apply_url)
        if resp.status_code in (404, 410):
            raise FormFetchError("form_unavailable", f"posting not found: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise FormFetchError("form_fetch_failed", f"HTTP {resp.status_code} from SmartRecruiters")
        return parse_smartrecruiters_form(resp.text, ref)
    except httpx.TransportError as exc:
        raise FormFetchError("form_fetch_failed", f"network error: {exc}") from exc
