"""Read-only extraction of a real Ashby application form.

Ashby's hosted job pages load the application form from the public
``non-user-graphql`` endpoint. That endpoint also answers plain HTTP GET
requests for read-only queries (Apollo's CSRF guard is satisfied by the
``apollo-require-preflight`` header), so this module issues exactly one GET
with a fixed, query-only document and parses the form definition.

Safety contract:

* Only HTTP GET to ``jobs.ashbyhq.com/api/non-user-graphql`` with the
  constant :data:`FORM_QUERY` (a ``query``, never a ``mutation``); redirects
  are not followed.
* Nothing is submitted or uploaded; the apply mutation is never referenced.
* A posting the API reports as missing raises ``form_unavailable``; any other
  failure raises ``form_fetch_failed``. There is no sample-form fallback.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
from app.prior_employment import tag_prior_employer

ASHBY_JOBS_HOST = "jobs.ashbyhq.com"
ASHBY_GRAPHQL_PATH = "/api/non-user-graphql"
ASHBY_GRAPHQL_URL = f"https://{ASHBY_JOBS_HOST}{ASHBY_GRAPHQL_PATH}"
#: Operations the read-only browser verifier may convert from POST to GET.
ASHBY_READ_OPS = frozenset({"ApiJobPosting", "ApiOrganizationFromHostedJobsPageName"})
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_FORM_SELECTION = "id sections { title fieldEntries { id field isRequired descriptionHtml } }"
FORM_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
    "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { "
    f"id title isListed applicationForm {{ {_FORM_SELECTION} }} surveyForms {{ {_FORM_SELECTION} }} }} }}"
)
LIVENESS_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
    "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { "
    "id title isListed } }"
)


@dataclass(frozen=True, slots=True)
class AshbyJobRef:
    org: str
    posting_id: str

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.org or ""):
            raise ValueError(f"invalid Ashby organization slug: {self.org!r}")
        if not _UUID_RE.match(self.posting_id or ""):
            raise ValueError(f"invalid Ashby posting id: {self.posting_id!r}")

    @property
    def page_url(self) -> str:
        return f"https://{ASHBY_JOBS_HOST}/{self.org}/{self.posting_id}/application"

    @property
    def vault_scopes(self) -> list[str]:
        org = self.org.lower()
        return [f"ashby:{org}", f"ashby:{org}:{self.posting_id}"]

    def graphql_params(self, query: str = FORM_QUERY) -> dict[str, str]:
        variables = {"organizationHostedJobsPageName": self.org, "jobPostingId": self.posting_id}
        return {"op": "ApiJobPosting", "query": query, "variables": json.dumps(variables, separators=(",", ":"))}


def _ids_ok(slug: str, posting_id: str) -> bool:
    return bool(_SLUG_RE.match(slug or "") and _UUID_RE.match(posting_id or ""))


def _hosted_ids(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url or "")
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() != ASHBY_JOBS_HOST or len(parts) < 2 or not _ids_ok(parts[0], parts[1]):
        return None
    return parts[0], parts[1]


def ashby_ref_for_job(job: Any) -> AshbyJobRef | None:
    """Ref from a hosted ``jobs.ashbyhq.com/{org}/{id}`` URL, else from Ashby discovery fields."""
    hosted = _hosted_ids(getattr(job, "url", "") or "")
    if hosted is not None:
        return AshbyJobRef(*hosted)
    if (getattr(job, "source", "") or "") != "ashby":
        return None
    slug = getattr(job, "board", None) or getattr(job, "company", None) or ""
    posting_id = getattr(job, "external_id", None) or ""
    return AshbyJobRef(slug, posting_id) if _ids_ok(slug, posting_id) else None


def graphql_get(ref: AshbyJobRef, query: str, *, client: httpx.Client | None = None, timeout: float = 20.0) -> httpx.Response:
    """One read-only GET to Ashby's public GraphQL endpoint. Raises httpx errors to the caller."""
    if not query.lstrip().startswith("query ") or "mutation" in query:
        raise FormFetchError("form_fetch_failed", "refusing a non-query GraphQL document")
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        request = http.build_request(
            "GET", ASHBY_GRAPHQL_URL, params=ref.graphql_params(query),
            headers={"Accept": "application/json", "apollo-require-preflight": "true"},
        )
        if request.method != "GET" or request.url.host != ASHBY_JOBS_HOST or request.url.path != ASHBY_GRAPHQL_PATH:
            raise FormFetchError("form_fetch_failed", "refusing non-GET or off-host form request")
        return http.send(request)
    finally:
        if owns_client:
            http.close()


def fetch_ashby_payload(ref: AshbyJobRef, *, client: httpx.Client | None = None, timeout: float = 20.0) -> dict[str, Any]:
    try:
        response = graphql_get(ref, FORM_QUERY, client=client, timeout=timeout)
    except httpx.HTTPError as exc:
        raise FormFetchError("form_fetch_failed", f"could not reach Ashby: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code != 200:
        raise FormFetchError("form_fetch_failed", f"Ashby returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise FormFetchError("form_fetch_failed", "Ashby returned a non-JSON response") from exc
    if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
        raise FormFetchError("form_fetch_failed", "Ashby GraphQL response carried errors or no data")
    return data["data"]


# --- parsing ------------------------------------------------------------------------------------

_TYPES = {
    "String": ControlType.TEXT,
    "Email": ControlType.EMAIL,
    "Phone": ControlType.TEL,
    "LongText": ControlType.TEXTAREA,
    "File": ControlType.FILE,
    "Location": ControlType.AUTOCOMPLETE,
    "ValueSelect": ControlType.SELECT,
    "MultiValueSelect": ControlType.MULTISELECT,
    "Boolean": ControlType.RADIO,
    "Number": ControlType.NUMBER,
    "Date": ControlType.DATE,
    "SocialLink": ControlType.TEXT,
}
# Field path (system or common custom path) -> canonical vault key.
_PATH_KEYS = {
    "_systemfield_name": "full_name",
    "_systemfield_email": "email",
    "_systemfield_phone": "phone",
    "_systemfield_resume": "resume",
    "_systemfield_location": "location",
    "_systemfield_cover_letter": "cover_letter",
    "currentcompany": "current_company",
    "currentlocation": "location",
    "phone": "phone",
    "linkedin": "linkedin_url",
    "github": "github_url",
    "portfolio": "website_url",
    "twitter": "twitter_url",
    "additionalinformation": "additional_information",
}
_TYPE_KEYS = {"Email": "email", "Phone": "phone"}
_VOLUNTARY_RE = re.compile(r"equal employment|eeo|self.?identif|demographic|voluntary", re.IGNORECASE)


def _field_data(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("field")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _file_key(title: str) -> str | None:
    lowered = title.lower()
    if "cover" in lowered:
        return "cover_letter"
    if "resume" in lowered or "résumé" in lowered or re.search(r"\bcv\b", lowered):
        return "resume"
    return None


def _canonical(path: str, raw_type: str, title: str, *, voluntary: bool, job_location: str) -> str | None:
    if voluntary:
        return demographic_key(title)
    known = _PATH_KEYS.get(path.lower())
    if known:
        return known
    if raw_type in _TYPE_KEYS:
        return _TYPE_KEYS[raw_type]
    if raw_type == "File":
        return _file_key(title)
    if raw_type == "Location" and "resid" in title.lower():
        return "location"
    if raw_type == "LongText" and title.strip().lower() == "cover letter":
        return "cover_letter_text"
    return shared_answer_key(title, job_location)


def _options(raw_type: str, data: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    if raw_type == "Boolean":
        return ["Yes", "No"], {"Yes": "true", "No": "false"}
    options: list[str] = []
    values: dict[str, str] = {}
    for item in data.get("selectableValues") or []:
        if not isinstance(item, dict):
            continue
        label = " ".join(str(item.get("label") or "").split())
        if label and label not in values:
            options.append(label)
            values[label] = str(item.get("value") or label)
    return options, values


def _section(path: str, section_title: str, survey: bool) -> str:
    if survey:
        return "demographic"
    if path.startswith("_systemfield_eeoc") or _VOLUNTARY_RE.search(section_title):
        return "eeoc"
    return "application"


def _entry_control(entry: dict[str, Any], *, section_title: str, survey: bool, job_location: str) -> FormControl | None:
    data = _field_data(entry)
    path = str(data.get("path") or "")
    if not path or data.get("isDeactivated"):
        return None
    title = " ".join(str(data.get("title") or "").split())
    raw_type = str(data.get("type") or "")
    control_type = _TYPES.get(raw_type, ControlType.UNKNOWN)
    section = _section(path, section_title, survey)
    voluntary = section != "application"
    canonical = _canonical(path, raw_type, title, voluntary=voluntary, job_location=job_location)
    options, option_values = _options(raw_type, data)
    section_note = [f"section={section_title[:80]}"] if section_title else []
    return FormControl(
        key=path,
        label=title or path,
        control_type=control_type,
        required=bool(entry.get("isRequired")) and not survey,
        options=options,
        name=path,
        input_type=raw_type,
        evidence=[f"ashby_api:{section}", f"type={raw_type}", f"path={path}", *section_note],
        confidence=0.2 if control_type is ControlType.UNKNOWN else 0.9,
        sensitive=is_sensitive_field(title, canonical, voluntary),
        legal=not voluntary and looks_like(title, LEGAL_PATTERNS),
        section=section,
        vault_keys=[path] + ([canonical] if canonical and canonical != path else []),
        option_values=option_values,
    )


def _form_controls(form: dict[str, Any] | None, *, survey: bool, job_location: str) -> list[FormControl]:
    controls: list[FormControl] = []
    for section in (form or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_title = " ".join(str(section.get("title") or "").split())
        for entry in section.get("fieldEntries") or []:
            control = _entry_control(entry, section_title=section_title, survey=survey, job_location=job_location) if isinstance(entry, dict) else None
            if control is not None:
                controls.append(control)
    return controls


def _warnings(posting: dict[str, Any], controls: list[FormControl]) -> list[str]:
    warnings = ["Ashby pages protect submission with reCAPTCHA; a live submission would require manual handoff"]
    if any(control.control_type is ControlType.UNKNOWN for control in controls):
        warnings.append("form contains field types this engine does not support")
    if posting.get("isListed") is False:
        warnings.append("posting is unlisted on the public board")
    return warnings


def parse_ashby_payload(data: dict[str, Any], ref: AshbyJobRef, *, job_location: str = "") -> RealForm:
    posting = data.get("jobPosting")
    if posting is None:
        raise FormFetchError("form_unavailable", "Ashby reports no such posting (closed, unlisted or removed)")
    if not isinstance(posting, dict) or not isinstance(posting.get("applicationForm"), dict):
        raise FormFetchError("form_unavailable", "posting has no Ashby-hosted application form")
    controls = _form_controls(posting["applicationForm"], survey=False, job_location=job_location)
    for survey in posting.get("surveyForms") or []:
        controls += _form_controls(survey if isinstance(survey, dict) else None, survey=True, job_location=job_location)
    if not controls:
        raise FormFetchError("form_fetch_failed", "Ashby application form contained no fields")
    first_by_key: dict[str, FormControl] = {}
    for control in controls:
        first_by_key.setdefault(control.key, control)
    unique = list(first_by_key.values())
    tag_prior_employer(unique, ref.org)
    return RealForm(
        platform="ashby",
        source="ashby_api",
        url=ref.page_url,
        title=str(posting.get("title") or ""),
        controls=unique,
        vault_scopes=ref.vault_scopes,
        warnings=_warnings(posting, unique),
        metadata={
            "org": ref.org,
            "posting_id": ref.posting_id,
            "form_id": posting["applicationForm"].get("id"),
            "page_url": ref.page_url,
            "job_location": job_location,
            "submit_boundary": "captcha_expected",
            "survey_forms": len(posting.get("surveyForms") or []),
        },
    )


def fetch_ashby_form(ref: AshbyJobRef, *, job_location: str = "", client: httpx.Client | None = None,
                     timeout: float = 20.0) -> RealForm:
    return parse_ashby_payload(fetch_ashby_payload(ref, client=client, timeout=timeout), ref, job_location=job_location)
