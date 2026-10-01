"""Optional Playwright inspection of a hosted application form.

Used to *verify* an API-derived form (Greenhouse, Lever, Ashby) and, for
Greenhouse, as a fallback when the API fails for transient reasons. The
browser session is strictly read-only:

* every request whose method is not GET/HEAD is aborted at the network layer,
  so no form post, upload, or analytics beacon can leave the browser. The one
  exception is opt-in per call (Ashby): a POST to Ashby's public GraphQL
  endpoint whose operation is on an allowlist and whose document is a plain
  ``query`` is re-issued as the equivalent GET (see :func:`graphql_get_rewrite`);
  anything else, including every mutation, is still aborted;
* the page is never clicked, typed into, or otherwise interacted with;
* only the posting's own hosted application URL is opened.

Playwright is an optional dependency (``pip install -e '.[browser]'`` plus
``python -m playwright install chromium``). When it is missing,
:class:`BrowserUnavailable` is raised and callers flag the dry-run.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from app.form_engine import ControlType, FormControl
from app.greenhouse_form import FormFetchError, GreenhouseJobRef, RealForm
from app.profile_vault import history_key

# Runs in the page. Reads DOM structure only; never mutates the page.
DOM_EXTRACT_JS = r"""
() => {
  const text = (el) => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const labelFor = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const joined = by.split(/\s+/).map((id) => text(document.getElementById(id))).filter(Boolean).join(' ');
      if (joined) return joined;
    }
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return text(lab);
    }
    const wrap = el.closest('label');
    return wrap ? text(wrap) : '';
  };
  const fields = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const id = el.id || '';
    const name = el.getAttribute('name') || '';
    if (!id && !name) continue;
    const type = (el.getAttribute('type') || '').toLowerCase();
    fields.push({
      tag: el.tagName.toLowerCase(),
      type,
      id,
      name,
      role: (el.getAttribute('role') || '').toLowerCase(),
      label: labelFor(el).replace(/\*$/, '').trim(),
      required: el.required || el.getAttribute('aria-required') === 'true',
      options: el.tagName.toLowerCase() === 'select' ? Array.from(el.options).map((o) => text(o)).filter(Boolean) : [],
    });
  }
  const body = text(document.body).slice(0, 4000);
  return {
    url: location.href,
    title: document.title,
    form_count: document.querySelectorAll('form').length,
    captcha: !!document.querySelector('[name="g-recaptcha-response"], iframe[src*="recaptcha"], iframe[src*="hcaptcha"], .h-captcha, .g-recaptcha'),
    body_text: body,
    fields,
  };
}
"""

_CHALLENGE_RE = re.compile(r"verify you are human|checking your browser|are you a robot|access denied|unusual traffic", re.IGNORECASE)
_IGNORED_IDS = re.compile(r"^(?:iti-\d+__search-input|g-recaptcha-response.*)$")
_IGNORED_NAMES = frozenset({"g-recaptcha-response", "h-captcha-response"})
_DOM_ALIASES = {"candidate-location": "location", "country": "phone_country"}
# Ashby prefixes radio/checkbox group names with the form id ("<uuid>_<path>").
_FORM_ID_PREFIX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_(?=.)")
GRAPHQL_REWRITE_HOST = "jobs.ashbyhq.com"
GRAPHQL_REWRITE_PATH = "/api/non-user-graphql"
GRAPHQL_REWRITE_URL = "https://jobs.ashbyhq.com/api/non-user-graphql"


class BrowserUnavailable(FormFetchError):
    def __init__(self, detail: str) -> None:
        super().__init__("form_fetch_failed", detail)


@dataclass(slots=True)
class DomField:
    tag: str
    type: str
    id: str
    name: str
    role: str
    label: str
    required: bool
    options: list[str] = field(default_factory=list)

    @property
    def form_key(self) -> str:
        """The API field name this DOM element most likely corresponds to."""
        ident = self.id or self.name
        if ident in _DOM_ALIASES:
            return _DOM_ALIASES[ident]
        if ident.isdigit():
            return f"demographic_{ident}"
        if self.name.startswith("gdpr_"):
            return self.name
        base = _FORM_ID_PREFIX.sub("", self.name or ident)
        return base.removesuffix("[]")


@dataclass(slots=True)
class DomSnapshot:
    url: str
    title: str
    form_count: int
    captcha: bool
    body_text: str
    fields: list[DomField]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomSnapshot:
        fields = []
        for raw in data.get("fields") or []:
            item = DomField(
                tag=str(raw.get("tag", "")),
                type=str(raw.get("type", "")),
                id=str(raw.get("id", "")),
                name=str(raw.get("name", "")),
                role=str(raw.get("role", "")),
                label=str(raw.get("label", "")),
                required=bool(raw.get("required")),
                options=[str(option) for option in raw.get("options") or []],
            )
            if item.type in {"hidden", "submit", "button", "search"} or _IGNORED_IDS.match(item.id) or item.name in _IGNORED_NAMES:
                continue
            fields.append(item)
        return cls(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            form_count=int(data.get("form_count") or 0),
            captcha=bool(data.get("captcha")),
            body_text=str(data.get("body_text", "")),
            fields=fields,
        )

    @property
    def challenge(self) -> str | None:
        """An interstitial challenge that hides the form (not the submit-time CAPTCHA)."""
        if not self.fields and _CHALLENGE_RE.search(self.body_text):
            return "anti_bot_challenge"
        return None


def _dom_control_type(item: DomField) -> ControlType:
    if item.role == "combobox":
        return ControlType.AUTOCOMPLETE
    if item.tag == "select":
        return ControlType.SELECT
    if item.tag == "textarea":
        return ControlType.TEXTAREA
    return {
        "file": ControlType.FILE,
        "checkbox": ControlType.CHECKBOX,
        "radio": ControlType.RADIO,
        "email": ControlType.EMAIL,
        "tel": ControlType.TEL,
        "date": ControlType.DATE,
        "number": ControlType.NUMBER,
        "": ControlType.TEXT,
        "text": ControlType.TEXT,
    }.get(item.type, ControlType.UNKNOWN)


def dom_control(item: DomField) -> FormControl:
    """A control known only from the rendered page.

    Its options cannot be read without interacting with the widget, so
    comboboxes get low confidence and always stop for review.
    """
    control_type = _dom_control_type(item)
    confidence = 0.6
    if control_type is ControlType.UNKNOWN or (control_type is ControlType.AUTOCOMPLETE and not item.options):
        confidence = 0.3
    key = item.form_key
    history = history_key(key, item.label)
    return FormControl(
        key=key,
        label=item.label or key,
        control_type=control_type,
        required=item.required,
        options=list(item.options),
        name=item.name or item.id,
        input_type=item.type,
        evidence=["greenhouse_dom", f"tag={item.tag}", f"id={item.id}", f"name={item.name}"],
        confidence=confidence,
        section="dom",
        vault_keys=[key, history] if history else [key],
    )


def _merge_submit_boundary(form: RealForm, snapshot: DomSnapshot) -> None:
    """A boundary already found in the fetched form (Lever/Ashby) is kept even if the render missed it."""
    previous = form.metadata.get("submit_boundary")
    form.metadata["submit_boundary"] = "captcha_detected" if snapshot.captcha else previous
    if snapshot.captcha and not previous:
        form.warnings.append("page carries a CAPTCHA at submit; a live submission would require manual handoff")


def reconcile(form: RealForm, snapshot: DomSnapshot) -> RealForm:
    """Cross-check an API-derived form against the rendered page.

    Required page fields missing from the API payload are added as
    review-only controls so the plan stops instead of skipping them.
    """
    api_keys = {control.key for control in form.controls}
    dom_keys = {item.form_key for item in snapshot.fields}
    added: list[str] = []
    for item in snapshot.fields:
        key = item.form_key
        if key in api_keys or not item.required:
            continue
        control = dom_control(item)
        form.controls.append(control)
        api_keys.add(key)
        added.append(key)
    missing_from_page = sorted(
        control.key for control in form.controls
        if control.section != "dom" and control.control_type is not ControlType.HIDDEN and control.key not in dom_keys
    )
    form.metadata["dom_verified"] = True
    form.metadata["dom_url"] = snapshot.url
    form.metadata["dom_only_required_fields"] = added
    form.metadata["api_fields_not_rendered"] = missing_from_page
    if added:
        form.warnings.append(f"page requires fields absent from the API payload: {', '.join(added)}")
    _merge_submit_boundary(form, snapshot)
    if snapshot.challenge:
        form.handoff = snapshot.challenge
    return form


def form_from_dom(ref: GreenhouseJobRef, snapshot: DomSnapshot) -> RealForm:
    """Fallback form built only from the rendered page."""
    if snapshot.challenge:
        return RealForm(
            platform="greenhouse", source="greenhouse_dom", url=snapshot.url, title=snapshot.title,
            controls=[], vault_scopes=ref.vault_scopes, handoff=snapshot.challenge,
            warnings=["page showed an anti-bot challenge instead of the form"],
            metadata={"board": ref.board, "job_id": ref.job_id, "dom_url": snapshot.url},
        )
    if not snapshot.fields:
        raise FormFetchError("form_fetch_failed", "rendered page contained no application fields")
    controls: list[FormControl] = []
    seen: set[str] = set()
    for item in snapshot.fields:
        control = dom_control(item)
        if control.key in seen:
            continue
        seen.add(control.key)
        controls.append(control)
    warnings = ["form built from the rendered page only; option lists were not read"]
    if snapshot.captcha:
        warnings.append("page carries a CAPTCHA at submit; a live submission would require manual handoff")
    return RealForm(
        platform="greenhouse",
        source="greenhouse_dom",
        url=snapshot.url,
        title=snapshot.title,
        controls=controls,
        vault_scopes=ref.vault_scopes,
        warnings=warnings,
        metadata={
            "board": ref.board,
            "job_id": ref.job_id,
            "dom_url": snapshot.url,
            "submit_boundary": "captcha_detected" if snapshot.captcha else None,
        },
    )


def _is_rewrite_endpoint(scheme: str, netloc: str, path: str) -> bool:
    return scheme == "https" and netloc.lower() == GRAPHQL_REWRITE_HOST and path == GRAPHQL_REWRITE_PATH


def _json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _is_read_only_query(query: Any) -> bool:
    if not isinstance(query, str) or not query.lstrip().startswith("query "):
        return False
    return re.search(r"\b(?:mutation|subscription)\b", query) is None


def graphql_get_rewrite(method: str, url: str, post_data: str | None, allowed_ops: Collection[str]) -> str | None:
    """The GET URL equivalent to an allowlisted, read-only GraphQL POST, or None to abort it.

    Only ``POST https://jobs.ashbyhq.com/api/non-user-graphql`` qualifies, only for
    operations in ``allowed_ops``, and only when the document is a single
    ``query`` with no ``mutation``/``subscription`` keyword anywhere.
    """
    parsed = urlparse(url)
    if method != "POST" or not allowed_ops or not _is_rewrite_endpoint(parsed.scheme, parsed.netloc, parsed.path):
        return None
    body = _json_object(post_data)
    operation = body.get("operationName") or (parse_qs(parsed.query).get("op") or [""])[0]
    query = body.get("query")
    if operation not in allowed_ops or not _is_read_only_query(query):
        return None
    params = {"op": operation, "query": query, "variables": json.dumps(body.get("variables") or {}, separators=(",", ":"))}
    return GRAPHQL_REWRITE_URL + "?" + urlencode(params)


async def _inspect(url: str, timeout_ms: int, graphql_ops: Collection[str] = ()) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise BrowserUnavailable("Playwright is not installed (pip install -e '.[browser]')") from exc

    blocked: list[str] = []
    rewritten: list[str] = []

    async def read_only(route: Any) -> None:
        request = route.request
        if request.method in {"GET", "HEAD"}:
            await route.continue_()
            return
        get_url = graphql_get_rewrite(request.method, request.url, request.post_data, graphql_ops)
        if get_url is None:
            blocked.append(f"{request.method} {request.url[:120]}")
            await route.abort()
            return
        rewritten.append(f"{request.method}->GET {request.url[:120]}")
        response = await route.fetch(url=get_url, method="GET", headers={"apollo-require-preflight": "true"})
        await route.fulfill(response=response)

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            raise BrowserUnavailable(f"could not launch Chromium: {exc}") from exc
        try:
            context = await browser.new_context(accept_downloads=False)
            await context.route("**/*", read_only)
            page = await context.new_page()
            response = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            if response is not None and response.status == 404:
                raise FormFetchError("form_unavailable", "hosted application page returned 404")
            data = await page.evaluate(DOM_EXTRACT_JS)
        finally:
            await browser.close()
    data["blocked_requests"] = blocked
    data["rewritten_requests"] = rewritten
    return data


def inspect_page(url: str, *, timeout_ms: int = 45000, graphql_ops: Collection[str] = ()) -> DomSnapshot:
    """Load a hosted application page read-only and return its field structure."""
    try:
        data = asyncio.run(_inspect(url, timeout_ms, graphql_ops))
    except FormFetchError:
        raise
    except Exception as exc:
        raise FormFetchError("form_fetch_failed", f"browser inspection failed: {exc.__class__.__name__}: {exc}") from exc
    return DomSnapshot.from_dict(data)


def inspect_hosted_form(ref: GreenhouseJobRef, *, timeout_ms: int = 45000) -> DomSnapshot:
    """Load the Greenhouse embed page read-only and return its field structure."""
    return inspect_page(ref.embed_url, timeout_ms=timeout_ms)
