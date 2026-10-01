"""Read-only extraction of a real Lever application form.

Lever serves the application form server-rendered at
``https://jobs.lever.co/{company}/{posting_id}/apply``. Standard fields are
plain inputs inside ``li.application-question`` blocks; custom questions
("cards") and voluntary surveys ship their full definition as JSON in hidden
``[baseTemplate]`` inputs. This module parses both into
:class:`~app.form_engine.FormControl` objects.

Safety contract:

* One HTTP GET to ``jobs.lever.co`` per fetch; redirects are not followed.
* Nothing is submitted, uploaded or posted; the form's POST action is never
  called. The page's hCaptcha is recorded as a submit boundary.
* Failures raise :class:`FormFetchError`; there is no fallback to a sample form.
"""
from __future__ import annotations

import json
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
    classify_question,
    demographic_key,
    is_sensitive_field,
    looks_like,
)

LEVER_JOBS_HOST = "jobs.lever.co"
LEVER_API_HOST = "api.lever.co"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_PAGE_BYTES = 4_000_000
USER_AGENT = "Mozilla/5.0 (compatible; HunterXJob/2; read-only form check)"


@dataclass(frozen=True, slots=True)
class LeverJobRef:
    company: str
    posting_id: str

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.company or ""):
            raise ValueError(f"invalid Lever company slug: {self.company!r}")
        if not _UUID_RE.match(self.posting_id or ""):
            raise ValueError(f"invalid Lever posting id: {self.posting_id!r}")

    @property
    def apply_url(self) -> str:
        return f"https://{LEVER_JOBS_HOST}/{self.company}/{self.posting_id}/apply"

    @property
    def api_url(self) -> str:
        return f"https://{LEVER_API_HOST}/v0/postings/{self.company}/{self.posting_id}"

    @property
    def vault_scopes(self) -> list[str]:
        company = self.company.lower()
        return [f"lever:{company}", f"lever:{company}:{self.posting_id}"]


def _ids_ok(slug: str, posting_id: str) -> bool:
    return bool(_SLUG_RE.match(slug or "") and _UUID_RE.match(posting_id or ""))


def _hosted_ids(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url or "")
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() != LEVER_JOBS_HOST or len(parts) < 2 or not _ids_ok(parts[0], parts[1]):
        return None
    return parts[0], parts[1]


def lever_ref_for_job(job: Any) -> LeverJobRef | None:
    """Ref from a hosted ``jobs.lever.co`` URL, else from Lever discovery fields."""
    hosted = _hosted_ids(getattr(job, "url", "") or "")
    if hosted is not None:
        return LeverJobRef(*hosted)
    if (getattr(job, "source", "") or "") != "lever":
        return None
    slug = getattr(job, "board", None) or getattr(job, "company", None) or ""
    posting_id = getattr(job, "external_id", None) or ""
    return LeverJobRef(slug, posting_id) if _ids_ok(slug, posting_id) else None


# --- HTML parsing -------------------------------------------------------------------------------

@dataclass(slots=True)
class _Block:
    label: list[str] = field(default_factory=list)
    required: bool = False
    inputs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label_text(self) -> str:
        return " ".join("".join(self.label).replace("✱", " ").split())


class _ApplyPageParser(HTMLParser):
    """Collects question blocks, hidden JSON templates and the CAPTCHA marker."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.form_found = False
        self.in_form = False
        self.captcha = False
        self.title: list[str] = []
        self.templates: list[tuple[str, str]] = []
        self.blocks: list[_Block] = []
        self._li_stack: list[_Block | None] = []
        self._div_depth = 0
        self._label_depth: int | None = None
        self._in_required = False
        self._option: dict[str, Any] | None = None
        self._in_h2 = False

    @property
    def _block(self) -> _Block | None:
        for block in reversed(self._li_stack):
            if block is not None:
                return block
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = attr.get("class", "").split()
        if tag == "form" and attr.get("id") == "application-form":
            self.form_found = self.in_form = True
        elif tag == "h2" and not self.title:
            self._in_h2 = True
        if "h-captcha" in classes or attr.get("name") == "h-captcha-response":
            self.captcha = True
        if not self.in_form:
            return
        self._start_in_form(tag, attr, classes)

    def _start_div(self, classes: list[str]) -> None:
        self._div_depth += 1
        if "application-label" in classes and self._block is not None and self._label_depth is None:
            self._label_depth = self._div_depth

    def _start_span(self, classes: list[str]) -> None:
        block = self._block
        if "required" in classes and self._label_depth is not None and block is not None:
            self._in_required = True
            block.required = True

    def _start_in_form(self, tag: str, attr: dict[str, str], classes: list[str]) -> None:
        if tag == "li":
            self._li_stack.append(_Block() if "application-question" in classes else None)
        elif tag == "div":
            self._start_div(classes)
        elif tag == "span":
            self._start_span(classes)
        elif tag in {"input", "select", "textarea"}:
            self._add_input(tag, attr)
        elif tag == "option" and self._block is not None and self._block.inputs:
            self._option = {"value": attr.get("value", ""), "text": []}

    def _add_input(self, tag: str, attr: dict[str, str]) -> None:
        name = attr.get("name", "")
        if name.endswith("[baseTemplate]"):
            self.templates.append((name, attr.get("value", "")))
            return
        block = self._block
        if block is None or not name:
            return
        block.inputs.append({
            "tag": tag,
            "name": name,
            "type": (attr.get("type") or ("textarea" if tag == "textarea" else tag)).lower(),
            "value": attr.get("value", ""),
            "required": "required" in attr,
            "options": [],
        })

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._in_h2 = False
        if not self.in_form:
            return
        if tag == "form":
            self.in_form = False
        elif tag == "li" and self._li_stack:
            block = self._li_stack.pop()
            if block is not None:
                self.blocks.append(block)
        elif tag == "div":
            if self._label_depth == self._div_depth:
                self._label_depth = None
            self._div_depth -= 1
        elif tag == "span":
            self._in_required = False
        elif tag == "option" and self._option is not None:
            block = self._block
            if block is not None and block.inputs:
                block.inputs[-1]["options"].append((" ".join("".join(self._option["text"]).split()), self._option["value"]))
            self._option = None

    def handle_data(self, data: str) -> None:
        if self._in_h2:
            self.title.append(data)
        if self._option is not None:
            self._option["text"].append(data)
        elif self._label_depth is not None and not self._in_required and self._block is not None:
            self._block.label.append(data)


# --- control building ---------------------------------------------------------------------------

# Standard Lever field name -> canonical answer-vault key (None: answered per question).
_STANDARD_KEYS: dict[str, str | None] = {
    "name": "full_name",
    "email": "email",
    "phone": "phone",
    "location": "location",
    "org": "current_company",
    "resume": "resume",
    "comments": "additional_information",
    "pronouns": "pronouns",
    "urls[LinkedIn]": "linkedin_url",
    "urls[GitHub]": "github_url",
    "urls[Portfolio]": "website_url",
    "urls[Twitter]": "twitter_url",
    "urls[Other]": None,
    "opportunityLocationId": None,
}
_NAMED_TYPES = {"email": ControlType.EMAIL, "phone": ControlType.TEL, "resume": ControlType.FILE}
_INPUT_TYPES = {
    "file": ControlType.FILE,
    "email": ControlType.EMAIL,
    "tel": ControlType.TEL,
    "textarea": ControlType.TEXTAREA,
    "select": ControlType.SELECT,
    "radio": ControlType.RADIO,
    "text": ControlType.TEXT,
    "url": ControlType.TEXT,
    "number": ControlType.NUMBER,
    "date": ControlType.DATE,
}
_CARD_TYPES = {
    "multiple-choice": ControlType.RADIO,
    "multiple-select": ControlType.MULTISELECT,
    "dropdown": ControlType.SELECT,
    "text": ControlType.TEXT,
    "textarea": ControlType.TEXTAREA,
    "file-upload": ControlType.FILE,
    "date": ControlType.DATE,
}
_SKIPPED_TYPES = frozenset({"hidden", "submit", "button"})


def _standard_type(name: str, inputs: list[dict[str, Any]]) -> ControlType:
    if name == "location":
        # Lever's location box is a type-ahead widget backed by a hidden field.
        return ControlType.AUTOCOMPLETE
    raw = inputs[0]["type"]
    if raw == "text" and name in _NAMED_TYPES:
        return _NAMED_TYPES[name]
    if raw == "checkbox":
        return ControlType.MULTISELECT if len(inputs) > 1 else ControlType.CHECKBOX
    return _INPUT_TYPES.get(raw, ControlType.UNKNOWN)


def _standard_options(inputs: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    options: list[str] = []
    values: dict[str, str] = {}
    for item in inputs:
        pairs = item["options"] if item["tag"] == "select" else (
            [(item["value"], item["value"])] if item["type"] in {"radio", "checkbox"} and item["value"] else []
        )
        for text, value in pairs:
            if text and value and text not in values:
                options.append(text)
                values[text] = value
    return options, values


def _standard_canonical(name: str, label: str, job_location: str) -> str | None:
    if name.startswith("eeo["):
        return demographic_key(label)
    if name in _STANDARD_KEYS:
        return _STANDARD_KEYS[name]
    if name.startswith("consent[") or looks_like(label, LEGAL_PATTERNS):
        return None
    return classify_question(label, job_location)


def _standard_control(name: str, inputs: list[dict[str, Any]], block: _Block, job_location: str) -> FormControl:
    label = block.label_text or name
    control_type = _standard_type(name, inputs)
    options, option_values = _standard_options(inputs)
    consent = name.startswith("consent[")
    eeo = name.startswith("eeo[")
    canonical = _standard_canonical(name, label, job_location)
    vault_keys = [name] + ([canonical] if canonical and canonical != name else [])
    return FormControl(
        key=name,
        label=label,
        control_type=control_type,
        required=block.required or any(item["required"] for item in inputs),
        options=options,
        name=name,
        input_type=inputs[0]["type"],
        evidence=["lever_page:application", f"type={inputs[0]['type']}", f"name={name}"],
        confidence=0.2 if control_type is ControlType.UNKNOWN else 0.85,
        # Optional marketing opt-ins are left unchecked (never consented on the owner's behalf).
        sensitive=not consent and is_sensitive_field(label, canonical, eeo),
        legal=not consent and not eeo and looks_like(label, LEGAL_PATTERNS),
        section="eeoc" if eeo else ("consent" if consent else "application"),
        vault_keys=vault_keys,
        option_values=option_values,
    )


def _block_controls(blocks: list[_Block], job_location: str) -> list[FormControl]:
    controls: list[FormControl] = []
    seen: set[str] = set()
    for block in blocks:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in block.inputs:
            name = item["name"]
            if item["type"] in _SKIPPED_TYPES or name.startswith(("cards[", "surveysResponses[")):
                continue
            grouped.setdefault(name, []).append(item)
        for name, inputs in grouped.items():
            if name in seen:
                continue
            seen.add(name)
            controls.append(_standard_control(name, inputs, block, job_location))
    return controls


def _template_fields(raw: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise FormFetchError("form_fetch_failed", "Lever question template is not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("fields"), list):
        raise FormFetchError("form_fetch_failed", "Lever question template has no fields")
    return data, [item for item in data["fields"] if isinstance(item, dict)]


def _card_options(item: dict[str, Any]) -> list[str]:
    options = [" ".join(str(option.get("text") or "").split()) for option in item.get("options") or [] if isinstance(option, dict)]
    return [option for option in options if option]


def _card_canonical(label: str, *, survey: bool, legal: bool, job_location: str) -> str | None:
    if survey:
        return demographic_key(label)
    return None if legal else classify_question(label, job_location)


def _card_control(key: str, item: dict[str, Any], *, survey: bool, job_location: str, origin: str) -> FormControl:
    label = " ".join(str(item.get("text") or "").split())
    raw_type = str(item.get("type") or "")
    control_type = _CARD_TYPES.get(raw_type, ControlType.UNKNOWN)
    options = _card_options(item)
    legal = not survey and looks_like(label, LEGAL_PATTERNS)
    canonical = _card_canonical(label, survey=survey, legal=legal, job_location=job_location)
    return FormControl(
        key=key,
        label=label or key,
        control_type=control_type,
        # Surveys are voluntary and shown only for some candidate locations.
        required=bool(item.get("required")) and not survey,
        options=options,
        name=key,
        input_type=raw_type,
        evidence=[origin, f"type={raw_type}", f"id={item.get('id', '')}"],
        confidence=0.2 if control_type is ControlType.UNKNOWN else 0.9,
        sensitive=is_sensitive_field(label, canonical, survey),
        legal=legal,
        section="demographic" if survey else "application",
        vault_keys=[key] + ([canonical] if canonical else []),
        option_values={option: option for option in options},
    )


_TEMPLATE_NAME_RE = re.compile(r"^(cards|surveysResponses)\[([0-9a-fA-F-]{36})\]\[baseTemplate\]$")


def _template_controls(templates: list[tuple[str, str]], job_location: str) -> list[FormControl]:
    controls: list[FormControl] = []
    for name, raw in templates:
        match = _TEMPLATE_NAME_RE.match(name)
        if match is None:
            continue
        kind, template_id = match.groups()
        data, fields = _template_fields(raw)
        survey = kind == "surveysResponses"
        origin = f"lever_page:{'survey' if survey else 'card'}:{' '.join(str(data.get('text') or '').split())[:80]}"
        for index, item in enumerate(fields):
            key = f"{kind}[{template_id}][responses][field{index}]" if survey else f"{kind}[{template_id}][field{index}]"
            controls.append(_card_control(key, item, survey=survey, job_location=job_location, origin=origin))
    return controls


def parse_lever_apply_page(html_text: str, ref: LeverJobRef, *, job_location: str = "") -> RealForm:
    """Convert a Lever ``/apply`` page into a :class:`RealForm`."""
    parser = _ApplyPageParser()
    parser.feed(html_text)
    parser.close()
    if not parser.form_found:
        raise FormFetchError("form_fetch_failed", "Lever page has no application form (posting may be closed or apply is external)")
    controls = _block_controls(parser.blocks, job_location) + _template_controls(parser.templates, job_location)
    if not controls:
        raise FormFetchError("form_fetch_failed", "Lever application form contained no fields")
    warnings: list[str] = ["Lever forms change with the candidate's selected location; voluntary surveys are location-dependent"]
    if any(control.control_type is ControlType.UNKNOWN for control in controls):
        warnings.append("form contains field types this engine does not support")
    if parser.captcha:
        warnings.append("page carries an hCaptcha at submit; a live submission would require manual handoff")
    return RealForm(
        platform="lever",
        source="lever_page",
        url=ref.apply_url,
        title=" ".join("".join(parser.title).split()),
        controls=controls,
        vault_scopes=ref.vault_scopes,
        warnings=warnings,
        metadata={
            "company": ref.company,
            "posting_id": ref.posting_id,
            "apply_url": ref.apply_url,
            "job_location": job_location,
            "submit_boundary": "captcha_detected" if parser.captcha else None,
            "surveys": sum(1 for name, _ in parser.templates if name.startswith("surveysResponses[")),
        },
    )


def fetch_lever_page(ref: LeverJobRef, *, client: httpx.Client | None = None, timeout: float = 20.0) -> str:
    """GET the public apply page. Never posts anything."""
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False,
                                  headers={"Accept": "text/html", "User-Agent": USER_AGENT})
    try:
        request = http.build_request("GET", ref.apply_url)
        if request.method != "GET" or request.url.host != LEVER_JOBS_HOST:
            raise FormFetchError("form_fetch_failed", "refusing non-GET or off-host form request")
        try:
            response = http.send(request)
        except httpx.HTTPError as exc:
            raise FormFetchError("form_fetch_failed", f"could not reach Lever: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code in {404, 410}:
            raise FormFetchError("form_unavailable", "posting not found on Lever (closed or removed)")
        if response.is_redirect:
            raise FormFetchError("form_fetch_failed", f"Lever redirected the apply page (HTTP {response.status_code}); posting may be closed")
        if response.status_code != 200:
            raise FormFetchError("form_fetch_failed", f"Lever returned HTTP {response.status_code}")
        if len(response.content) > MAX_PAGE_BYTES:
            raise FormFetchError("form_fetch_failed", "Lever apply page is unexpectedly large")
        return response.text
    finally:
        if owns_client:
            http.close()


def fetch_lever_form(ref: LeverJobRef, *, job_location: str = "", client: httpx.Client | None = None,
                     timeout: float = 20.0) -> RealForm:
    return parse_lever_apply_page(fetch_lever_page(ref, client=client, timeout=timeout), ref, job_location=job_location)
