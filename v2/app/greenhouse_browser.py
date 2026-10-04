"""Optional Playwright inspection of a hosted application form.

Used to *verify* an API-derived form (Greenhouse, Lever, Ashby) and, for
Greenhouse, as a fallback when the API fails for transient reasons. The
browser session is strictly read-only:

* every request whose method is not GET is aborted at the network layer
  (HEAD included), so no form post, upload, or analytics beacon can leave the
  browser. The one
  exception is opt-in per call (Ashby): a POST to Ashby's public GraphQL
  endpoint whose operation is on an allowlist and whose document is a plain
  ``query`` is re-issued as the equivalent GET (see :func:`graphql_get_rewrite`);
  anything else, including every mutation, is still aborted;
* the page is never clicked, typed into, or otherwise interacted with;
* the page cannot submit a form: an init script in every frame cancels
  ``submit`` events and replaces ``form.submit()``/``form.requestSubmit()``
  (``submit()`` fires no event) with a recorder, so every attempt is recorded
  and refused, and the session then fails closed;
* service workers are blocked, so no worker can make requests that bypass the
  network-layer router;
* only the posting's own hosted application URL is opened;
* every request the page makes is recorded (method, URL, resource type, and
  whether it was continued, aborted, or rewritten), together with main-frame
  navigations and form submit attempts, so a dry-run carries network
  evidence that nothing was posted. With ``BROWSER_TRACE_DIR`` set, a
  Playwright trace zip is written, integrity-checked, and hashed as well.

Playwright is an optional dependency (``pip install -e '.[browser]'`` plus
``python -m playwright install chromium``). When it is missing,
:class:`BrowserUnavailable` is raised and callers flag the dry-run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
import zipfile
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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

# Added to every frame before any page script runs. It records every form submit
# attempt and refuses it: ``submit`` events are cancelled (registered first, in the
# capture phase on ``window``, so page listeners cannot hide them), and
# ``HTMLFormElement.submit()`` -- which dispatches no ``submit`` event -- and
# ``requestSubmit()`` are replaced by recorders. It never triggers anything itself.
SUBMIT_MONITOR_JS = r"""
(() => {
  const seen = [];
  Object.defineProperty(window, '__hunterxSubmitEvents', { value: seen, enumerable: false });
  const record = (form, via) => {
    if (seen.length < 50) {
      seen.push({ via, action: String((form && form.action) || ''), method: String((form && form.method) || '') });
    }
  };
  window.addEventListener('submit', (event) => {
    record(event.target, 'submit_event');
    event.preventDefault();
  }, true);
  const refuse = (via) => function () { record(this, via); };
  for (const [name, via] of [['submit', 'form.submit()'], ['requestSubmit', 'form.requestSubmit()']]) {
    Object.defineProperty(HTMLFormElement.prototype, name, {
      value: refuse(via), writable: false, configurable: false, enumerable: false,
    });
  }
})();
"""
READ_SUBMIT_EVENTS_JS = "() => (window.__hunterxSubmitEvents || []).slice(0, 50)"
MAX_RECORDED_REQUESTS = 500
MAX_SUBMIT_EVENTS = 50
# Only GET may leave the browser (HEAD is not a GET, so it is aborted and counted too).
READ_ONLY_METHODS = frozenset({"GET"})

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
    # Network/trace evidence from the read-only session (see _inspect); empty for stored snapshots.
    evidence: dict[str, Any] = field(default_factory=dict)

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
            evidence=dict(data.get("evidence") or {}),
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


def _attach_evidence(form: RealForm, snapshot: DomSnapshot) -> None:
    """Keep the session's network evidence with the form; warn if the page tried to write anything."""
    if not snapshot.evidence:
        return
    form.metadata["browser_evidence"] = {
        **snapshot.evidence,
        "dom_fields": [{
            "key": item.form_key, "id": item.id, "name": item.name, "tag": item.tag, "type": item.type,
            "required": item.required, "options": item.options[:50],
        } for item in snapshot.fields],
    }
    # Informational only: a real page commonly fires analytics beacons (POST), which the
    # router aborts. Allowlisted GraphQL reads re-issued as GET are by design and not warned.
    aborted = int(snapshot.evidence.get("aborted") or 0)
    submits = len(snapshot.evidence.get("submit_events") or [])
    if aborted:
        form.warnings.append(f"page attempted {aborted} non-GET request(s) during read-only inspection (for example "
                             "analytics beacons); all were aborted before leaving the browser")
    if submits:
        form.warnings.append(f"page attempted {submits} form submission(s) during read-only inspection; all were refused")


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
    _attach_evidence(form, snapshot)
    if added:
        form.warnings.append(f"page requires fields absent from the API payload: {', '.join(added)}")
    _merge_submit_boundary(form, snapshot)
    if snapshot.challenge:
        form.handoff = snapshot.challenge
    return form


def form_from_dom(ref: GreenhouseJobRef, snapshot: DomSnapshot) -> RealForm:
    """Fallback form built only from the rendered page."""
    if snapshot.challenge:
        challenged = RealForm(
            platform="greenhouse", source="greenhouse_dom", url=snapshot.url, title=snapshot.title,
            controls=[], vault_scopes=ref.vault_scopes, handoff=snapshot.challenge,
            warnings=["page showed an anti-bot challenge instead of the form"],
            metadata={"board": ref.board, "job_id": ref.job_id, "dom_url": snapshot.url},
        )
        _attach_evidence(challenged, snapshot)
        return challenged
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
    form = RealForm(
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
    _attach_evidence(form, snapshot)
    return form


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


def trace_record(path: Path) -> dict[str, Any]:
    """Integrity check and hash of a written Playwright trace zip."""
    record: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not record["exists"]:
        return {**record, "zip_ok": False}
    data = path.read_bytes()
    record["bytes"] = len(data)
    record["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            record["zip_ok"] = archive.testzip() is None and any(name.endswith(".trace") for name in names)
            record["entries"] = len(names)
    except zipfile.BadZipFile:
        record["zip_ok"] = False
    return record


def _trace_path(trace_dir: str | None) -> Path | None:
    if not trace_dir:
        return None
    folder = Path(trace_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return folder / f"browser-trace-{stamp}-{uuid.uuid4().hex[:8]}.zip"


class _NetworkLog:
    """Every request the page made, and what the read-only router did with it."""

    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []
        self.total = 0
        self.non_get = 0
        self.aborted = 0
        self.rewritten = 0
        self.navigations: list[str] = []
        self.main_frame_documents = 0

    def record(self, request: Any, action: str) -> None:
        self.total += 1
        if request.is_navigation_request() and request.frame.parent_frame is None:
            self.main_frame_documents += 1
        if request.method not in READ_ONLY_METHODS:
            self.non_get += 1
        if action == "aborted":
            self.aborted += 1
        elif action == "rewritten_to_get":
            self.rewritten += 1
        if len(self.requests) < MAX_RECORDED_REQUESTS:
            self.requests.append({
                "method": request.method,
                "url": request.url[:300],
                "resource_type": request.resource_type,
                "action": action,
            })

    def summary(self) -> dict[str, Any]:
        return {
            "request_count": self.total,
            "requests_truncated": self.total > len(self.requests),
            "non_get_attempts": self.non_get,
            "aborted": self.aborted,
            "rewritten_to_get": self.rewritten,
            "requests": self.requests,
            "main_frame_navigations": self.navigations,
            "main_frame_document_requests": self.main_frame_documents,
        }


def _origin_and_path(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url or "")
    return parsed.scheme, parsed.netloc.lower(), parsed.path


def untrusted_session_reasons(network: dict[str, Any], submit_events: list[Any], form_url: str, final_url: str) -> list[str]:
    """Why a read-only session cannot vouch for the form: the page tried to submit or left it.

    A DOM read after the page fired a submit or navigated its main frame away
    describes some other page (a confirmation, an error page), not the form.
    """
    reasons = []
    if submit_events:
        reasons.append(f"page attempted {len(submit_events)} form submission(s)")
    documents = int(network.get("main_frame_document_requests") or 0)
    if documents > 1:
        reasons.append(f"page started {documents - 1} extra main-frame navigation(s)")
    if form_url and _origin_and_path(final_url) != _origin_and_path(form_url):
        reasons.append(f"page ended on {final_url[:120]} instead of the form")
    return reasons


class _ReadOnlyRouter:
    """Route handler: GET continues, allowlisted GraphQL reads become GETs, everything else is aborted."""

    def __init__(self, network: _NetworkLog, graphql_ops: Collection[str]) -> None:
        self.network = network
        self.graphql_ops = graphql_ops
        self.blocked: list[str] = []
        self.rewritten: list[str] = []

    async def __call__(self, route: Any) -> None:
        request = route.request
        if request.method in READ_ONLY_METHODS:
            self.network.record(request, "continued")
            await route.continue_()
            return
        get_url = graphql_get_rewrite(request.method, request.url, request.post_data, self.graphql_ops)
        if get_url is None:
            self.network.record(request, "aborted")
            self.blocked.append(f"{request.method} {request.url[:120]}")
            await route.abort()
            return
        self.network.record(request, "rewritten_to_get")
        self.rewritten.append(f"{request.method}->GET {request.url[:120]}")
        response = await route.fetch(url=get_url, method="GET", headers={"apollo-require-preflight": "true"})
        await route.fulfill(response=response)


async def _submit_attempts(page: Any, evidence: dict[str, Any]) -> list[Any]:
    """Submit attempts recorded in every frame of the page (an iframe can host a form too)."""
    from playwright.async_api import Error as PlaywrightError

    attempts: list[Any] = []
    unreadable: list[str] = []
    for frame in page.frames:
        try:
            attempts.extend(await frame.evaluate(READ_SUBMIT_EVENTS_JS))
        except PlaywrightError:  # a frame detached or navigated while being read
            unreadable.append(frame.url[:300])
    evidence["unreadable_frames"] = unreadable
    return attempts[:MAX_SUBMIT_EVENTS]


async def _read_page(context: Any, url: str, timeout_ms: int, network: _NetworkLog, evidence: dict[str, Any]) -> dict[str, Any]:
    """Open the form, read its structure, and refuse a session in which the page submitted or left the form."""
    page = await context.new_page()
    page.on("framenavigated", lambda frame: network.navigations.append(frame.url[:300]) if frame == page.main_frame else None)
    response = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    if response is not None and response.status == 404:
        raise FormFetchError("form_unavailable", "hosted application page returned 404")
    data = await page.evaluate(DOM_EXTRACT_JS)
    evidence["submit_events"] = await _submit_attempts(page, evidence)
    form_url = response.url if response is not None else url
    reasons = untrusted_session_reasons(network.summary(), evidence["submit_events"], form_url, str(data.get("url", "")))
    if reasons:
        if network.aborted:
            reasons.append(f"it also attempted {network.aborted} non-GET request(s), all aborted")
        raise FormFetchError("form_fetch_failed", "read-only page inspection is not trustworthy: " + "; ".join(reasons))
    return data


async def _inspect(url: str, timeout_ms: int, graphql_ops: Collection[str] = (),
                   trace_dir: str | None = None) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise BrowserUnavailable("Playwright is not installed (pip install -e '.[browser]')") from exc

    network = _NetworkLog()
    router = _ReadOnlyRouter(network, graphql_ops)
    trace_path = _trace_path(trace_dir)
    evidence: dict[str, Any] = {"launcher": "playwright.chromium.launch", "headless": True, "service_workers": "block"}

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            raise BrowserUnavailable(f"could not launch Chromium: {exc}") from exc
        evidence["browser_version"] = browser.version
        evidence["executable_path"] = playwright.chromium.executable_path
        try:
            # Service workers are blocked: requests a worker makes are not guaranteed to pass context.route().
            context = await browser.new_context(accept_downloads=False, service_workers="block")
            await context.add_init_script(SUBMIT_MONITOR_JS)
            await context.route("**/*", router)
            if trace_path is not None:
                await context.tracing.start(screenshots=True, snapshots=True)
            try:
                data = await _read_page(context, url, timeout_ms, network, evidence)
            finally:
                if trace_path is not None:
                    await context.tracing.stop(path=str(trace_path))
                await context.close()
        finally:
            await browser.close()
    evidence.update(network.summary())
    evidence["trace"] = trace_record(trace_path) if trace_path is not None else None
    data["blocked_requests"] = router.blocked
    data["rewritten_requests"] = router.rewritten
    data["evidence"] = evidence
    return data


def inspect_page(url: str, *, timeout_ms: int = 45000, graphql_ops: Collection[str] = (),
                 trace_dir: str | None = None) -> DomSnapshot:
    """Load a hosted application page read-only and return its field structure."""
    try:
        data = asyncio.run(_inspect(url, timeout_ms, graphql_ops, trace_dir))
    except FormFetchError:
        raise
    except Exception as exc:
        raise FormFetchError("form_fetch_failed", f"browser inspection failed: {exc.__class__.__name__}: {exc}") from exc
    return DomSnapshot.from_dict(data)


def inspect_hosted_form(ref: GreenhouseJobRef, *, timeout_ms: int = 45000, trace_dir: str | None = None) -> DomSnapshot:
    """Load the Greenhouse embed page read-only and return its field structure."""
    return inspect_page(ref.embed_url, timeout_ms=timeout_ms, trace_dir=trace_dir)
