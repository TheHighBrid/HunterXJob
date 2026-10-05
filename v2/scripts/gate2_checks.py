"""Gate 2 checks (docs/RECOVERY_CONTRACT.md), kept separate from the runner (scripts/gate2.py).

Pure helpers: posting choice, the read-only network verdict for a real page,
the field-mapping summary, the per-run checks, the outcome, and the report.
They reuse the Gate 1 helpers (trace-zip verification, process checks).
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from gate1_checks import _from_playwright_install, _pipe_not_port, inspect_trace_zip, is_browser

GREENHOUSE_API_HOST = "boards-api.greenhouse.io"

# Clearly fake identity. Nothing personal; no résumé, so no file is ever planned for upload.
FAKE_IDENTITY = {
    "first_name": "Test",
    "last_name": "Applicant",
    "email": "test@example.com",
    "phone": "555-0100",
}

OUTCOME_OK = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_BLOCKED = "blocked_by_bot_check"
BLOCK_STATUSES = frozenset({401, 403, 429, 503})

NOT_PROVEN = {
    "submission_or_submit_click": False,
    "in_page_typing_of_values": False,
    "file_upload": False,
    "answers_for_employer_specific_questions": False,
    "captcha_solving_or_bypass": False,
    "more_than_one_posting_or_employer": False,
    "lever_and_ashby": False,
    "scheduler_or_continuous_run_path": False,
    "discovery_scoring_and_materials_approval_path": False,
    "android_termux_proot_execution": False,
}
NOT_PROVEN_NOTES = {
    "submission_or_submit_click": "Dry-run only; the submit control is never clicked and live submission stays locked.",
    "in_page_typing_of_values": "The production engine plans values and verifies the page read-only; it never types.",
    "file_upload": "No résumé is in the fake vault, so nothing is planned for upload and nothing is uploaded.",
    "answers_for_employer_specific_questions": "Only a fake identity is in the vault; employer questions go to review.",
    "captcha_solving_or_bypass": "Never attempted. A challenge is recorded as a finding and the run stops.",
    "more_than_one_posting_or_employer": "Exactly one real posting is loaded once.",
    "lever_and_ashby": "Greenhouse only.",
    "scheduler_or_continuous_run_path": "The dry-run is triggered by the API route, not by a scheduled cycle.",
    "discovery_scoring_and_materials_approval_path": "The job is seeded at ready_to_apply from the public boards API.",
    "android_termux_proot_execution": "Frozen by the recovery contract; not exercised.",
}


def choose_posting(listing: dict[str, Any], job_id: str | None = None, location_hint: str = "") -> dict[str, Any]:
    """Pick one open posting from a boards-API listing (by id, else the first whose location matches)."""
    jobs = [item for item in listing.get("jobs") or [] if item.get("id") and item.get("absolute_url")]
    if job_id is not None:
        jobs = [item for item in jobs if str(item["id"]) == str(job_id)]
    elif location_hint:
        jobs = [item for item in jobs if location_hint.lower() in str((item.get("location") or {}).get("name", "")).lower()]
    if not jobs:
        raise ValueError("no open posting matches")
    item = jobs[0]
    return {"id": str(item["id"]), "title": str(item.get("title", "")).strip(), "absolute_url": item["absolute_url"],
            "location": str((item.get("location") or {}).get("name", "")), "updated_at": item.get("updated_at")}


def _origin_and_path(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url or "")
    return parts.scheme, parts.netloc.lower(), parts.path


def request_counts(evidence: dict[str, Any]) -> dict[str, Any]:
    """Browser requests by method, by action (continued/aborted), and by host."""
    requests = evidence.get("requests") or []
    return {
        "total": evidence.get("request_count"),
        "by_method": dict(Counter(item.get("method") for item in requests)),
        "by_action": dict(Counter(item.get("action") for item in requests)),
        "by_host": dict(Counter(urlsplit(str(item.get("url", ""))).netloc for item in requests).most_common()),
        "non_get_attempts": evidence.get("non_get_attempts"),
        "non_get_aborted": evidence.get("aborted"),
        "rewritten_to_get": evidence.get("rewritten_to_get"),
        "truncated": evidence.get("requests_truncated"),
        "main_frame_document_requests": evidence.get("main_frame_document_requests"),
        "main_frame_navigation_events": len(evidence.get("main_frame_navigations") or []),
    }


def evaluate_read_only(evidence: dict[str, Any], http_log: list[dict[str, Any]], api_host: str | Collection[str],
                       *, allow_rewrites: bool = False) -> list[str]:
    """Why the session cannot count as zero-submit / zero-non-GET-sent on a real page.

    A real page may *attempt* a write (an analytics beacon, say). The router aborts it
    before it leaves the browser; that is recorded and allowed. A non-GET that was not
    aborted, a rewrite, a submit attempt, or a second main-frame navigation is not.

    ``api_host`` is the host (or hosts) the app itself may GET. ``allow_rewrites`` is only
    for Ashby, whose page reads its form through allowlisted GraphQL queries that the
    router re-issues as GET (so they still leave the browser as GET).
    """
    hosts = {api_host} if isinstance(api_host, str) else set(api_host)
    if not evidence:
        return ["the API result carries no browser evidence (browser verification did not complete)"]
    problems = []
    contained = {"aborted", "rewritten_to_get"} if allow_rewrites else {"aborted"}
    for item in evidence.get("requests") or []:
        if item.get("method") != "GET" and item.get("action") not in contained:
            problems.append(f"non-GET request was not aborted: {item.get('method')} {item.get('url')}")
    attempts, aborted = int(evidence.get("non_get_attempts") or 0), int(evidence.get("aborted") or 0)
    rewritten = int(evidence.get("rewritten_to_get") or 0) if allow_rewrites else 0
    if attempts != aborted + rewritten:
        problems.append(f"{attempts} non-GET attempt(s) but only {aborted} aborted"
                        + (f" and {rewritten} rewritten to GET" if allow_rewrites else ""))
    if evidence.get("rewritten_to_get") and not allow_rewrites:
        problems.append(f"{evidence['rewritten_to_get']} request(s) were rewritten (no rewrite is allowed for Greenhouse)")
    if evidence.get("submit_events"):
        problems.append(f"page attempted {len(evidence['submit_events'])} form submission(s)")
    # Exactly one document load. A real page's client-side router may update the URL in place
    # (history API), which fires framenavigated without loading a document; that is allowed only
    # while the main frame stays on the form's own origin and path.
    documents = int(evidence.get("main_frame_document_requests") or 0)
    if documents != 1:
        problems.append(f"main frame loaded {documents} document(s), expected exactly 1")
    navigations = evidence.get("main_frame_navigations") or []
    if not navigations:
        problems.append("no main-frame navigation was recorded")
    left = [url for url in navigations if _origin_and_path(url) != _origin_and_path(navigations[0])]
    if left:
        problems.append(f"main frame left the form: {left[:3]}")
    if evidence.get("service_workers") != "block":
        problems.append("service workers were not blocked in the browser context")
    for entry in http_log:
        if entry.get("local_api"):
            continue
        if entry.get("method") != "GET":
            problems.append(f"app sent {entry.get('method')} to {entry.get('host')}")
        if entry.get("host") not in hosts:
            problems.append(f"app HTTP request left the boards API host: {entry.get('host')}")
    return problems


def field_mapping(result: dict[str, Any], validation: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """How the real form's fields mapped onto the (fake) vault."""
    form = result.get("form") or validation.get("form") or {}
    metadata = form.get("metadata") or {}
    planned = validation.get("planned") or {}
    blocked = validation.get("blocked_fields") or []
    dom_fields = evidence.get("dom_fields") or []
    return {
        "dom_fields_detected": len(dom_fields),
        "dom_required_fields": sum(1 for item in dom_fields if item.get("required")),
        "dom_field_kinds": dict(Counter(item.get("type") or item.get("tag") for item in dom_fields)),
        "form_controls": form.get("fields_total"),
        "form_controls_required": form.get("fields_required"),
        "sections": form.get("sections"),
        "planned": planned,
        "planned_count": len(planned or {}),
        "needs_review_count": len(blocked),
        "needs_review_required": sorted(item["key"] for item in blocked if item.get("required")),
        "needs_review_by_section": dict(Counter(item.get("section") for item in blocked)),
        "needs_review_by_reason": dict(Counter(item.get("reason") for item in blocked)),
        "needs_review": blocked,
        "dom_only_required_fields": metadata.get("dom_only_required_fields"),
        "api_fields_not_rendered": metadata.get("api_fields_not_rendered"),
        "submit_boundary": metadata.get("submit_boundary"),
        "blockers": validation.get("blockers"),
    }


class RunContext:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.response = data.get("response") or {}
        self.result = self.response.get("json") or {}
        self.ledger = data.get("ledger") or {"validation": {}, "evidence": [], "application_stage": None}
        self.validation = self.ledger.get("validation") or {}
        self.form = self.result.get("form") or self.validation.get("form") or {}
        self.metadata = self.form.get("metadata") or {}
        self.evidence = self.metadata.get("browser_evidence") or {}
        self.browsers = [item for item in data.get("sampled_processes") or [] if is_browser(item)]
        self.api_host = data.get("api_host") or GREENHOUSE_API_HOST
        # Other platforms (scripts/dryrun_batch.py): the app's own hosts, DOM keys that prove the
        # form rendered, and whether allowlisted read-only GraphQL rewrites are expected (Ashby).
        self.api_hosts = list(data.get("api_hosts") or [self.api_host])
        self.allow_rewrites = bool(data.get("allow_rewrites"))
        self.required_dom_keys = list(data.get("required_dom_keys") or
                                      ([] if data.get("required_dom_key_substrings") else ["first_name", "email"]))
        self.required_dom_key_substrings = list(data.get("required_dom_key_substrings") or [])


def challenge_detected(ctx: RunContext) -> str | None:
    """A bot check / block: an interstitial challenge, a block status, or a challenge handoff."""
    handoff = ctx.form.get("handoff") or ctx.result.get("reason")
    if handoff == "anti_bot_challenge":
        return "anti_bot_challenge"
    status = ctx.evidence.get("main_document_status")
    if status in BLOCK_STATUSES:
        return f"http_{status}"
    return None


def check_api(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    result = ctx.result
    ok = (ctx.response.get("status_code") == 200 and result.get("status") in {"dry_run_complete", "needs_review"}
          and result.get("submitted") is False and result.get("reason") not in {"form_fetch_failed", "form_unavailable"}
          and bool(ctx.data.get("api_shutdown_clean")))
    return ok, {"status_code": ctx.response.get("status_code"), "status": result.get("status"),
                "reason": result.get("reason"), "detail": result.get("detail"), "submitted": result.get("submitted"),
                "elapsed_s": ctx.response.get("elapsed_s"), "api_shutdown_clean": ctx.data.get("api_shutdown_clean")}


def check_posting_live(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    job_id = str(ctx.data.get("job_id"))
    app_http = [entry for entry in ctx.data.get("http_log") or [] if not entry.get("local_api")]
    if ctx.data.get("probe_id_in_query"):  # Ashby: the posting id travels in the GraphQL variables
        probes = [entry for entry in app_http if job_id in entry.get("query", "")]
    else:
        probes = [entry for entry in app_http if entry.get("path", "").endswith(f"/{job_id}") and not entry.get("query")]
    ok = bool(probes) and probes[0].get("status") == 200
    return ok, {"liveness_probe": probes[:1]}


def check_launch(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    executable = str(ctx.evidence.get("executable_path") or "")
    own_binary = _from_playwright_install(ctx.browsers, executable)
    pipe = _pipe_not_port(ctx.browsers)
    ok = (ctx.evidence.get("launcher") == "playwright.chromium.launch" and own_binary and pipe
          and "ms-playwright" in executable and not ctx.data.get("cdp_in_app_code"))
    return ok, {"launcher": ctx.evidence.get("launcher"), "browser_version": ctx.evidence.get("browser_version"),
                "executable_path": executable, "browser_processes_seen": len(ctx.browsers),
                "from_playwright_install": own_binary, "remote_debugging_pipe_not_port": pipe,
                "connect_over_cdp_in_app_code": ctx.data.get("cdp_in_app_code")}


def check_form_rendered(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    fields = ctx.evidence.get("dom_fields") or []
    keys = {item.get("key") for item in fields}
    challenge = challenge_detected(ctx)
    ok = (ctx.metadata.get("dom_verified") is True and ctx.evidence.get("main_document_status") == 200
          and set(ctx.required_dom_keys) <= keys and challenge is None
          and all(any(part in str(key or "").lower() for key in keys) for part in ctx.required_dom_key_substrings))
    return ok, {"dom_verified": ctx.metadata.get("dom_verified"), "dom_url": ctx.metadata.get("dom_url"),
                "main_document_status": ctx.evidence.get("main_document_status"), "dom_fields": len(fields),
                "challenge": challenge, "submit_boundary": ctx.metadata.get("submit_boundary")}


def _artifact(path_text: str | None, kind: str, run_dir: Path) -> str | None:
    if path_text:
        return path_text
    found = sorted((run_dir / "traces").glob(f"*.{kind}")) if run_dir else []
    return str(found[-1]) if found else None


def check_trace(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    reported = ctx.evidence.get("trace") or {}
    path = _artifact(reported.get("path"), "zip", Path(ctx.data.get("run_dir") or "."))
    marker = ctx.data.get("trace_marker")  # the form page's path (dryrun_batch.py); Greenhouse embed by default
    trace = inspect_trace_zip(Path(path or "/nonexistent"), marker) if marker else inspect_trace_zip(Path(path or "/nonexistent"))
    ok = bool(trace.get("ok") and reported.get("zip_ok") and reported.get("sha256") == trace.get("sha256"))
    ctx.data["trace_sha256"] = trace.get("sha256")
    return ok, {**trace, "app_reported_sha256": reported.get("sha256")}


def check_screenshot(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    import hashlib

    reported = ctx.evidence.get("screenshot") or {}
    path = Path(_artifact(reported.get("path"), "png", Path(ctx.data.get("run_dir") or ".")) or "/nonexistent")
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    ok = bool(digest) and digest == reported.get("sha256")
    return ok, {"path": str(path), "sha256": digest, "app_reported_sha256": reported.get("sha256"),
                "bytes": path.stat().st_size if path.is_file() else None, "error": reported.get("error")}


def expected_ledger_payload(ctx: RunContext, kind: str) -> dict[str, Any]:
    from app.pipeline import browser_ledger_entry

    browser = browser_ledger_entry(ctx.form)
    if kind == "dry_run":
        payload: dict[str, Any] = {"filled": ctx.ledger.get("answers") or {}, "form_source": ctx.form.get("source"),
                                   "materials": ctx.validation.get("materials") or []}
    else:
        payload = {"planned": ctx.validation.get("planned") or {},
                   "needs_review": [item["key"] for item in ctx.validation.get("blocked_fields") or []],
                   "blockers": ctx.validation.get("blockers") or [], "form_source": ctx.form.get("source")}
    if browser:
        payload["browser"] = browser
    return payload


def check_ledger(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    from app.evidence import payload_hash

    rows = ctx.ledger.get("evidence") or []
    # Exactly one dry-run row. Other rows may only be the (never sufficient) material decisions
    # recorded when materials were generated and approved before the run (dryrun_batch.py).
    runs = [item for item in rows if item.get("kind") in {"dry_run", "dry_run_review"}]
    others = [item for item in rows if item not in runs]
    row = runs[0] if len(runs) == 1 else {}
    kind = row.get("kind")
    recomputed = payload_hash(expected_ledger_payload(ctx, kind)) if kind in {"dry_run", "dry_run_review"} else None
    ok = (kind in {"dry_run", "dry_run_review"} and row.get("sufficient") is False
          and row.get("payload_hash") == recomputed
          and f"sha256={ctx.data.get('trace_sha256')}" in (row.get("confirmation_text") or "")
          and all(str(item.get("kind")).startswith("materials_") and item.get("sufficient") is False for item in others))
    return ok, {"rows": rows, "recomputed_payload_hash": recomputed,
                "application_stage": ctx.ledger.get("application_stage"), "events": ctx.ledger.get("events")}


def check_read_only(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    problems = evaluate_read_only(ctx.evidence, ctx.data.get("http_log") or [], ctx.api_hosts,
                                  allow_rewrites=ctx.allow_rewrites)
    app_http = [entry for entry in ctx.data.get("http_log") or [] if not entry.get("local_api")]
    return not problems, {"problems": problems, "browser": request_counts(ctx.evidence),
                          "browser_requests": ctx.evidence.get("requests"),
                          "aborted_requests": [item for item in ctx.evidence.get("requests") or []
                                               if item.get("action") == "aborted"],
                          "submit_events": ctx.evidence.get("submit_events"),
                          "main_frame_navigations": ctx.evidence.get("main_frame_navigations"),
                          "app_http_requests": app_http,
                          "app_http_by_method": dict(Counter(entry.get("method") for entry in app_http))}


def check_cleanup(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    leftover, sampled = ctx.data.get("leftover_children") or [], ctx.data.get("leftover_sampled") or []
    ok = not leftover and not sampled and bool(ctx.browsers)
    return ok, {"browser_pids_seen": [item["pid"] for item in ctx.browsers],
                "all_descendant_pids_seen": [item["pid"] for item in ctx.data.get("sampled_processes") or []],
                "leftover_children": leftover, "leftover_sampled": sampled}


def check_unattended(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    return bool(ctx.data.get("stdin_closed")), {"stdin": "not a terminal (spawned run process)"}


CHECKS = (
    ("api_completed_cleanly", check_api),
    ("posting_confirmed_open", check_posting_live),
    ("chromium_launched_by_playwright", check_launch),
    ("real_form_rendered_without_challenge", check_form_rendered),
    ("trace_zip_valid_and_hashed", check_trace),
    ("screenshot_saved_and_hashed", check_screenshot),
    ("evidence_persisted_in_ledger", check_ledger),
    ("zero_submit_and_zero_non_get_sent", check_read_only),
    ("browser_and_children_gone_after_shutdown", check_cleanup),
    ("no_human_input", check_unattended),
)
# Checks that must hold even when a bot check stops the run (it must still stop safely).
SAFETY_CHECKS = ("api_completed_cleanly", "chromium_launched_by_playwright", "trace_zip_valid_and_hashed",
                 "screenshot_saved_and_hashed", "zero_submit_and_zero_non_get_sent",
                 "browser_and_children_gone_after_shutdown", "no_human_input")


def run_checks(data: dict[str, Any]) -> tuple[dict[str, Any], RunContext]:
    ctx = RunContext(data)
    checks = {}
    for name, check in CHECKS:
        ok, detail = check(ctx)
        checks[name] = {"ok": bool(ok), "detail": detail}
    return checks, ctx


def outcome(checks: dict[str, Any], challenge: str | None) -> str:
    if challenge:
        safe = all(checks[name]["ok"] for name in SAFETY_CHECKS if name in checks)
        return OUTCOME_BLOCKED if safe else OUTCOME_FAIL
    return OUTCOME_OK if all(item["ok"] for item in checks.values()) else OUTCOME_FAIL


def build_report(run: dict[str, Any], *, posting: dict[str, Any], mode: str) -> dict[str, Any]:
    checks = run.get("checks") or {}
    verdict = run.get("outcome") or OUTCOME_FAIL
    green = verdict == OUTCOME_OK and mode == "live"
    return {
        "gate": "gate2",
        "contract": "docs/RECOVERY_CONTRACT.md",
        "mode": mode,
        "generated_at": datetime.now(UTC).isoformat(),
        "host": {"platform": sys.platform, "python": sys.version.split()[0], "ci": bool(os.environ.get("CI"))},
        "posting": posting,
        "fake_identity": FAKE_IDENTITY,
        "outcome": verdict,
        "challenge": run.get("challenge"),
        "failed_checks": sorted(name for name, item in checks.items() if not item["ok"]),
        "proven": {
            "one_real_public_greenhouse_posting_dry_run": green,
            "same_runtime_as_gate1_api_dry_run_route_playwright_chromium": green,
            "fake_identity_only": True,
            "trace_zip_valid_and_hashed": checks.get("trace_zip_valid_and_hashed", {}).get("ok", False),
            "screenshot_of_the_form_as_seen": checks.get("screenshot_saved_and_hashed", {}).get("ok", False),
            "evidence_persisted_in_ledger": checks.get("evidence_persisted_in_ledger", {}).get("ok", False),
            "zero_submit_and_zero_non_get_sent": checks.get("zero_submit_and_zero_non_get_sent", {}).get("ok", False),
            "clean_shutdown_no_leftover_processes": all(
                checks.get(name, {}).get("ok", False)
                for name in ("browser_and_children_gone_after_shutdown", "browser_gone_after_child_exit",
                             "no_process_outlived_the_run")),
        },
        "not_proven": NOT_PROVEN,
        "not_proven_notes": NOT_PROVEN_NOTES,
        "field_mapping": run.get("field_mapping"),
        "request_counts": (checks.get("zero_submit_and_zero_non_get_sent") or {}).get("detail", {}).get("browser"),
        "trace_sha256": (checks.get("trace_zip_valid_and_hashed") or {}).get("detail", {}).get("sha256"),
        "run": run,
    }
