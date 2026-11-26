#!/usr/bin/env python3
"""Greenhouse + Lever dry-run batch from job URLs or board tokens, with a zero-submit proof per job.

A thin front end over ``scripts/dryrun_batch.py`` (itself the Gate 2 path extended to Lever):
nothing here opens a page, types, clicks, or talks to an employer on its own. It only

1. turns job URLs (``https://job-boards.greenhouse.io/<board>/jobs/<id>``,
   ``https://jobs.lever.co/<board>/<uuid>``), ``platform:board:id`` specs, or board tokens
   (``greenhouse:<board>`` / ``lever:<board>``, expanded with ONE GET to the public board API
   and a keyword/location filter) into ``dryrun_batch`` targets;
2. hands them to ``dryrun_batch.run_batch`` in ``live`` mode: one GET to confirm each posting is
   open, then a freshly spawned app process with ``APPLICATION_MODE=dry_run`` and
   ``ALLOW_LIVE_SUBMISSION=false`` reads the public form (GET), plans answers from the vault,
   and loads the hosted form once read-only in Playwright's own Chromium (non-GET aborted,
   form submission refused in the page, no typing, no clicking, no upload);
3. re-reads every run's saved observations and writes ``job-NN.json`` plus
   ``ats-summary.json`` / ``ats-summary.md`` with: fields seen, planned, needs review,
   unresolved required fields, readback-verified count, Truth Guard findings, and an explicit
   ``live_submits`` / ``non_get_requests_sent`` proof. Any field reported as filled or verified
   without evidence is listed under ``false_clean`` and makes the exit status non-zero.

Every output is labelled ``evidence: provisional`` (Gate B is not certified). The identity
defaults to the made-up example profile (``examples/profile.example.yaml``), so planned counts
measure the adapters, not the owner's real coverage. ``--profile/--answers/--guardrails`` may
point at the owner's files; output must then stay outside the repository (enforced always).

Never runs in CI and never in pytest (``tests/test_ats_dryrun_batch.py`` covers it offline)::

    python scripts/ats_dryrun_batch.py --out /private/dir --pause 25 \
        https://job-boards.greenhouse.io/affirm/jobs/7821078003 lever:waveapps \
        --keywords "fraud|aml|risk" --locations "canada|ottawa" --per-board 2
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dryrun_batch
from batch_checks import sanitize_board

SUPPORTED = ("greenhouse", "lever")
EVIDENCE_LABEL = "provisional (Gate B not certified; read-only dry-run, nothing typed or submitted)"
USER_AGENT = "HunterXJob-ats-dryrun-batch/1.0 (read-only dry-run verification)"
LEVER_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
GREENHOUSE_ID_RE = re.compile(r"^\d{4,}$")
GREENHOUSE_HOSTS = ("job-boards.greenhouse.io", "boards.greenhouse.io", "job-boards.eu.greenhouse.io",
                    "boards.eu.greenhouse.io")
# Non-GET requests to these are application writes. They must never leave the browser or the app.
APPLY_ENDPOINTS = (
    re.compile(r"^https?://boards-api\.greenhouse\.io/v1/boards/[^/]+/jobs/\d+", re.IGNORECASE),
    re.compile(r"^https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/.*(?:application|job_app|/jobs/\d+)", re.IGNORECASE),
    re.compile(r"^https?://jobs\.lever\.co/[^/]+/[0-9a-f-]{36}/apply", re.IGNORECASE),
    re.compile(r"^https?://api\.lever\.co/v0/postings/[^/]+/[0-9a-f-]{36}/apply", re.IGNORECASE),
)
DEFAULT_KEYWORDS = r"fraud|aml|anti-money|risk|collections?|compliance|financial crime|kyc|analyst|banking|credit"
DEFAULT_LOCATIONS = r"ottawa|gatineau|canada|remote.*\bca\b"


# --------------------------------------------------------------------------- targets (pure)

def _platform_target(platform: str, board: str, job_id: str) -> dict[str, Any]:
    board = sanitize_board(board)
    pattern = GREENHOUSE_ID_RE if platform == "greenhouse" else LEVER_ID_RE
    if not pattern.fullmatch(job_id):
        raise SystemExit(f"bad {platform} job id {job_id!r}")
    return {"platform": platform, "board": board, "id": job_id, "company": board}


def _from_url(spec: str) -> dict[str, Any] | None:
    parts = urlsplit(spec)
    host = parts.netloc.lower()
    segments = [item for item in parts.path.split("/") if item]
    query = parse_qs(parts.query)
    if host in GREENHOUSE_HOSTS:
        if len(segments) >= 3 and segments[1] == "jobs":
            return _platform_target("greenhouse", segments[0], segments[2])
        if segments[-1:] == ["job_app"] and query.get("for") and query.get("token"):
            return _platform_target("greenhouse", query["for"][0], query["token"][0])
    if host == "boards-api.greenhouse.io" and len(segments) >= 5 and segments[:2] == ["v1", "boards"]:
        return _platform_target("greenhouse", segments[2], segments[4])
    if host == "jobs.lever.co" and len(segments) >= 2:
        return _platform_target("lever", segments[0], segments[1])
    if host == "api.lever.co" and len(segments) >= 4 and segments[:2] == ["v0", "postings"]:
        return _platform_target("lever", segments[2], segments[3])
    return None


def parse_target(spec: str) -> dict[str, Any]:
    """One job (``id`` set) or one board token (``id`` None) from a URL or ``platform:board[:id]``."""
    spec = spec.strip()
    if spec.startswith(("http://", "https://")):
        target = _from_url(spec)
        if target is None:
            raise SystemExit(f"not a Greenhouse or Lever job URL: {spec}")
        return target
    pieces = spec.split(":")
    if len(pieces) not in (2, 3) or pieces[0] not in SUPPORTED:
        raise SystemExit(f"bad target {spec!r}: use a job URL, platform:board:id, or platform:board "
                         f"(platform in {SUPPORTED})")
    if len(pieces) == 3:
        return _platform_target(*pieces)
    return {"platform": pieces[0], "board": sanitize_board(pieces[1]), "id": None, "company": pieces[1]}


def board_listing_url(platform: str, board: str) -> str:
    if platform == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    return f"https://api.lever.co/v0/postings/{board}?mode=json"


def _listing_rows(platform: str, payload: Any) -> list[tuple[str, str, str]]:
    """(id, title, location text) for every posting in a public board listing."""
    if platform == "greenhouse":
        return [(str(job.get("id")), str(job.get("title") or ""), str((job.get("location") or {}).get("name") or ""))
                for job in (payload or {}).get("jobs") or []]
    rows = []
    for job in payload or []:
        categories = job.get("categories") or {}
        places = [categories.get("location") or "", *(categories.get("allLocations") or []),
                  job.get("workplaceType") or ""]
        rows.append((str(job.get("id")), str(job.get("text") or ""), " ; ".join(str(item) for item in places if item)))
    return rows


def select_from_board(board_target: dict[str, Any], payload: Any, *, keywords: str, locations: str,
                      limit: int) -> list[dict[str, Any]]:
    """Postings on a board whose title matches ``keywords`` and location matches ``locations``."""
    title_re, place_re = re.compile(keywords, re.IGNORECASE), re.compile(locations, re.IGNORECASE)
    picked = []
    for job_id, title, place in _listing_rows(board_target["platform"], payload):
        if title_re.search(title) and place_re.search(place):
            target = _platform_target(board_target["platform"], board_target["board"], job_id)
            picked.append({**target, "company": board_target.get("company") or target["board"],
                           "listed_title": title, "listed_location": place})
        if limit and len(picked) >= limit:
            break
    return picked


def expand_targets(specs: Iterable[str], fetch_json: Callable[[str], tuple[int, Any]], *, keywords: str,
                   locations: str, per_board: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve specs into job targets; board tokens cost ONE GET each. Returns (targets, discovery log)."""
    targets: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for spec in specs:
        target = parse_target(spec)
        found = [target]
        if target["id"] is None:
            url = board_listing_url(target["platform"], target["board"])
            status, payload = fetch_json(url)
            found = select_from_board(target, payload, keywords=keywords, locations=locations,
                                      limit=per_board) if status == 200 else []
            log.append({"spec": spec, "method": "GET", "url": url, "status": status, "selected": len(found)})
        for item in found:
            key = (item["platform"], item["board"], item["id"])
            if key not in seen:
                seen.add(key)
                targets.append(item)
    return targets, log


# --------------------------------------------------------------------------- per-job summary (pure)

def _norm_key(key: Any) -> str:
    return re.sub(r"\[\]$", "", str(key or "")).strip()


def _is_apply_endpoint(url: str) -> bool:
    return any(pattern.search(url or "") for pattern in APPLY_ENDPOINTS)


def _observed_form(observations: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    response = (observations.get("response") or {}).get("json") or {}
    validation = (observations.get("ledger") or {}).get("validation") or {}
    form = response.get("form") or validation.get("form") or {}
    metadata = form.get("metadata") or {}
    return response, metadata, metadata.get("browser_evidence") or {}


def request_proof(evidence: dict[str, Any], http_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Count what left the browser and the app. ``aborted`` non-GETs never left the browser."""
    requests = evidence.get("requests") or []
    non_get = [item for item in requests if str(item.get("method") or "GET").upper() != "GET"]
    sent = [item for item in non_get if item.get("action") != "aborted"]
    app_non_get = [entry for entry in http_log if not entry.get("local_api")
                   and str(entry.get("method") or "GET").upper() != "GET"]
    app_urls = [f"https://{entry.get('host')}{entry.get('path') or ''}" for entry in app_non_get]
    # The router's own counters survive a truncated request list; trust whichever reports more sent.
    counted = int(evidence.get("non_get_attempts") or 0) - int(evidence.get("aborted") or 0)
    browser_sent = max(len(sent), counted)
    return {
        "browser_evidence": bool(evidence), "requests_truncated": bool(evidence.get("requests_truncated")),
        "browser_requests": len(requests),
        "browser_non_get_attempted": max(len(non_get), int(evidence.get("non_get_attempts") or 0)),
        "browser_non_get_aborted": int(evidence.get("aborted") or 0) if evidence else 0,
        "browser_non_get_sent": browser_sent,
        "app_non_get_sent": len(app_non_get),
        "non_get_requests_sent": browser_sent + len(app_non_get),
        "apply_endpoint_non_get_attempted": sum(1 for item in non_get if _is_apply_endpoint(item.get("url") or ""))
        + sum(1 for url in app_urls if _is_apply_endpoint(url)),
        "apply_endpoint_non_get_sent": sum(1 for item in sent if _is_apply_endpoint(item.get("url") or ""))
        + sum(1 for url in app_urls if _is_apply_endpoint(url)),
        "page_submit_attempts_refused": [{"via": item.get("via"), "action": item.get("action")}
                                         for item in evidence.get("submit_events") or []],
        "sent_non_get_urls": [item.get("url") for item in sent] + app_urls,
    }


def _live_submits(response: dict[str, Any], observations: dict[str, Any], proof: dict[str, Any]) -> int:
    rows = (observations.get("ledger") or {}).get("evidence") or []
    return (int(response.get("submitted") is True) + sum(1 for row in rows if row.get("kind") == "submission")
            + int(proof["apply_endpoint_non_get_sent"]))


def field_counts(response: dict[str, Any], metadata: dict[str, Any], evidence: dict[str, Any],
                 mapping: dict[str, Any]) -> dict[str, Any]:
    dom = evidence.get("dom_fields") or []
    dom_keys = {_norm_key(item.get("key")) for item in dom if item.get("key")}
    required = {_norm_key(item.get("key")) for item in dom if item.get("key") and item.get("required")}
    planned = {_norm_key(key) for key in (mapping.get("planned") or {})}
    review = {_norm_key(item.get("key")) for item in mapping.get("needs_review") or []}
    readbacks = [item for item in evidence.get("readbacks") or [] if item.get("matched") is True]
    return {
        "fields_seen": len(dom_keys), "dom_inputs_seen": len(dom), "required_seen": len(required),
        "form_controls": mapping.get("form_controls"),
        "planned": len(planned), "needs_review": len(review),
        "unresolved_required": sorted(required - planned),
        "required_not_planned_nor_reviewed": sorted(required - planned - review),
        "readback_verified": len(readbacks),
        "readback_exercised": bool(evidence.get("readbacks")),
        "claimed_filled": len(response.get("filled") or {}),
        "dom_keys": dom_keys, "planned_keys": planned,
        "api_fields_not_rendered": [_norm_key(key) for key in metadata.get("api_fields_not_rendered") or []],
    }


def planned_not_rendered(counts: dict[str, Any], dom_rendered: bool) -> list[str]:
    """Planned answers for API fields the hosted form never rendered (they could never be typed)."""
    if not dom_rendered:
        return []
    hidden = set(counts["api_fields_not_rendered"]) or (counts["planned_keys"] - counts["dom_keys"])
    return sorted(counts["planned_keys"] & hidden)


def false_clean(response: dict[str, Any], counts: dict[str, Any], dom_rendered: bool) -> list[dict[str, Any]]:
    """Fields reported as filled/verified without the evidence to back it (escalate to the owner)."""
    problems = []
    claimed = {_norm_key(key) for key in response.get("filled") or {}}
    phantom = set(planned_not_rendered(counts, dom_rendered))
    for key in sorted(claimed & phantom):
        problems.append({"key": key, "problem": "reported filled but the field is not rendered on the hosted form"})
    if response.get("status") == "dry_run_complete" and counts["required_not_planned_nor_reviewed"]:
        problems.append({"keys": counts["required_not_planned_nor_reviewed"],
                         "problem": "run reported complete while required DOM fields were neither planned nor sent to review"})
    if counts["readback_verified"] > len(counts["planned_keys"]):
        problems.append({"problem": "more readback-verified fields than planned fields"})
    return problems


_BLOCK_RE = re.compile(r"HTTP (?:403|406|429|503)|http_(?:403|406|429|503)")


def blocked_reason(report: dict[str, Any], response: dict[str, Any], checks: dict[str, Any]) -> str | None:
    """A rate limit or block status, which must never be read as "posting closed" or "form empty"."""
    api = ((checks.get("api_completed_cleanly") or {}).get("detail") or {})
    text = " ".join(str(item or "") for item in (report.get("error"), response.get("reason"), api.get("detail")))
    match = _BLOCK_RE.search(text)
    return match.group(0).replace("http_", "HTTP ") if match else None


def truth_guard_findings(observations: dict[str, Any], checks: dict[str, Any]) -> dict[str, Any]:
    materials = observations.get("materials") or []
    unbacked = ((checks.get("planned_answers_backed_by_verified_facts") or {}).get("detail") or {}).get("unbacked") or []
    return {
        "material_findings": [{"kind": row.get("kind"), "status": row.get("status"), "issues": row.get("truth_guard")}
                              for row in materials if row.get("truth_guard")],
        "claims_to_avoid_hits": sum(len(row.get("claims_hits") or []) for row in materials),
        "unbacked_planned_answers": [{"key": item.get("key"), "label": (item.get("label") or "")[:160],
                                      "value": item.get("value"), "problem": item.get("problem")} for item in unbacked],
    }


def job_summary(report: dict[str, Any], observations: dict[str, Any] | None) -> dict[str, Any]:
    """The per-job JSON: counts, Truth Guard, zero-submit proof, false-clean list."""
    observations = observations or {}
    posting, checks = report.get("posting") or {}, report.get("checks") or {}
    response, metadata, evidence = _observed_form(observations)
    proof = request_proof(evidence, observations.get("http_log") or [])
    counts = field_counts(response, metadata, evidence, report.get("field_mapping") or {})
    rendered = bool(evidence.get("dom_fields"))
    clean = false_clean(response, counts, rendered)
    phantom = planned_not_rendered(counts, rendered)
    live = _live_submits(response, observations, proof)
    for key in ("dom_keys", "planned_keys", "api_fields_not_rendered"):
        counts.pop(key)
    return {
        "evidence": EVIDENCE_LABEL, "index": report.get("index"),
        "job": {key: posting.get(key) for key in ("platform", "board", "id", "company", "title", "location", "url",
                                                  "verified_open_at")},
        "outcome": report.get("outcome"), "app_status": response.get("status"), "challenge": report.get("challenge"),
        "failed_checks": report.get("failed_checks") or [], "error": report.get("error"),
        "blocked": blocked_reason(report, response, checks),
        "form_rendered": rendered, **counts, "planned_not_rendered": phantom,
        "truth_guard": truth_guard_findings(observations, checks),
        "false_clean": clean,
        "proof": {"live_submits": live, **proof,
                  "allow_live_submission": (observations.get("settings") or {}).get("allow_live_submission"),
                  "application_mode": (observations.get("settings") or {}).get("application_mode"),
                  "ok": live == 0 and proof["non_get_requests_sent"] == 0},
        "run_dir": report.get("run_dir"),
    }


def batch_summary(jobs: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    ran = [job for job in jobs if job.get("outcome") not in {"skipped_closed", "probe_error"}]
    proofs = [job["proof"] for job in jobs]
    return {
        "evidence": EVIDENCE_LABEL, "generated_at": datetime.now(UTC).isoformat(), **meta,
        "jobs": len(jobs), "ran": len(ran), "outcomes": dict(Counter(job.get("outcome") for job in jobs)),
        "live_submits": sum(item["live_submits"] for item in proofs),
        "non_get_requests_sent": sum(item["non_get_requests_sent"] for item in proofs),
        "apply_endpoint_non_get_attempted": sum(item["apply_endpoint_non_get_attempted"] for item in proofs),
        "browser_non_get_aborted": sum(item["browser_non_get_aborted"] for item in proofs),
        "page_submit_attempts_refused": sum(len(item["page_submit_attempts_refused"]) for item in proofs),
        "false_clean_jobs": [job["index"] for job in jobs if job["false_clean"]],
        "blocked_jobs": {str(job["index"]): job["blocked"] for job in jobs if job.get("blocked")},
        "planned_not_rendered_jobs": [job["index"] for job in jobs if job.get("planned_not_rendered")],
        "readback_verified": sum(job.get("readback_verified") or 0 for job in ran),
        "planned": sum(job.get("planned") or 0 for job in ran),
        "needs_review": sum(job.get("needs_review") or 0 for job in ran),
        "fields_seen": sum(job.get("fields_seen") or 0 for job in ran),
        "proof_ok": all(item["ok"] for item in proofs),
    }


def summary_markdown(summary: dict[str, Any], jobs: list[dict[str, Any]]) -> str:
    lines = [f"# ATS dry-run batch: evidence {summary['evidence']}", "",
             f"live_submits={summary['live_submits']} non_get_requests_sent={summary['non_get_requests_sent']} "
             f"false_clean_jobs={summary['false_clean_jobs'] or 'none'} blocked={summary['blocked_jobs'] or 'none'}", "",
             "| # | ATS | Company | Title | Outcome | Fields | Planned | Review | Unresolved req | Readback ok "
             "| Live submits | Non-GET sent | Aborted | False clean | Blocked |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for job in jobs:
        posting, proof = job["job"], job["proof"]
        lines.append(f"| {job['index']} | {posting.get('platform')} | {posting.get('company')} | {posting.get('title')} "
                     f"| {job['outcome']} | {job.get('fields_seen')} | {job.get('planned')} | {job.get('needs_review')} "
                     f"| {len(job.get('unresolved_required') or [])} | {job.get('readback_verified')} "
                     f"| {proof['live_submits']} | {proof['non_get_requests_sent']} | {proof['browser_non_get_aborted']} "
                     f"| {len(job['false_clean'])} | {job.get('blocked') or ''} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- orchestration

def _http_get_json(url: str) -> tuple[int, Any]:
    import httpx

    with httpx.Client(timeout=30, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(url)
    return response.status_code, response.json() if response.status_code == 200 else None


def default_inputs(out: Path) -> dict[str, Any]:
    """The made-up example profile plus the rehearsal answers and guardrails (no real identity)."""
    answers, guardrails = out / "example-answers.yaml", out / "example-guardrails.yaml"
    answers.write_text(yaml.safe_dump(dryrun_batch.REHEARSAL_ANSWERS), encoding="utf-8")
    guardrails.write_text(yaml.safe_dump(dryrun_batch.REHEARSAL_GUARDRAILS), encoding="utf-8")
    return dryrun_batch.load_inputs(dryrun_batch.rehearsal_profile(out), answers, guardrails)


def _load_observations(report: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(report.get("run_dir") or "") / "observations.json"
    return json.loads(path.read_text(encoding="utf-8")) if report.get("run_dir") and path.is_file() else None


def write_outputs(out: Path, reports: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    jobs = [job_summary(report, _load_observations(report)) for report in reports]
    for job in jobs:
        (out / f"job-{int(job['index'] or 0):02d}.json").write_text(json.dumps(job, indent=2, default=str),
                                                                    encoding="utf-8")
    summary = batch_summary(jobs, meta)
    (out / "ats-summary.json").write_text(json.dumps({**summary, "rows": jobs}, indent=2, default=str), encoding="utf-8")
    (out / "ats-summary.md").write_text(summary_markdown(summary, jobs), encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("specs", nargs="*", help="job URLs, platform:board:id, or platform:board (board token)")
    parser.add_argument("--targets-file", type=Path, help="one spec per line (# comments allowed)")
    parser.add_argument("--out", type=Path, required=True, help="output directory (outside the repository)")
    parser.add_argument("--keywords", default=DEFAULT_KEYWORDS, help="title regex for board tokens")
    parser.add_argument("--locations", default=DEFAULT_LOCATIONS, help="location regex for board tokens")
    parser.add_argument("--per-board", type=int, default=2, help="postings to take per board token")
    parser.add_argument("--profile", type=Path, help="profile YAML (default: made-up example profile)")
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--guardrails", type=Path)
    parser.add_argument("--pause", type=float, default=25.0, help="seconds between runs (minimum 20)")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--discover-only", action="store_true", help="resolve targets and stop (GETs only)")
    return parser


def _read_specs(args: argparse.Namespace) -> list[str]:
    specs = list(args.specs)
    if args.targets_file:
        specs += [line.strip() for line in args.targets_file.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    if not specs:
        raise SystemExit("no targets: pass job URLs / board tokens or --targets-file")
    return specs


def _inputs(args: argparse.Namespace, out: Path) -> dict[str, Any]:
    owner = (args.profile, args.answers, args.guardrails)
    if any(owner) and not all(owner):
        raise SystemExit("--profile, --answers and --guardrails go together")
    return dryrun_batch.load_inputs(*owner) if all(owner) else default_inputs(out)


def main(argv: list[str] | None = None, *, fetch_json: Callable[[str], tuple[int, Any]] | None = None,
         run_batch: Callable[..., Any] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if os.environ.get("CI"):
        raise SystemExit("ats_dryrun_batch never runs in CI; it contacts real employer postings")
    if args.pause < 20:
        raise SystemExit("--pause must be at least 20 seconds between live runs")
    out = args.out.resolve()
    dryrun_batch.refuse_repo_output(out)
    out.mkdir(parents=True, exist_ok=True)
    targets, discovery = expand_targets(_read_specs(args), fetch_json or _http_get_json, keywords=args.keywords,
                                        locations=args.locations, per_board=args.per_board)
    (out / "targets.json").write_text(json.dumps({"targets": targets, "discovery": discovery}, indent=2),
                                      encoding="utf-8")
    print(f"{len(targets)} target(s): " + ", ".join(f"{t['platform']}:{t['board']}:{t['id']}" for t in targets))
    if args.discover_only or not targets:
        return 0 if targets else 4
    inputs = _inputs(args, out)
    dryrun_batch.clear_reports_from(out, 1)
    reports, stopped = (run_batch or dryrun_batch.run_batch)(targets, inputs, out, mode="live", pause=args.pause,
                                                             timeout=args.timeout)
    summary = write_outputs(out, reports, {"stopped": stopped, "profile_sha256": inputs["profile_sha256"],
                                           "discovery": discovery})
    print(json.dumps({key: summary[key] for key in ("evidence", "ran", "outcomes", "live_submits",
                                                    "non_get_requests_sent", "false_clean_jobs", "blocked_jobs",
                                                    "proof_ok")}, indent=2))
    if not summary["proof_ok"] or summary["false_clean_jobs"]:
        return 1
    return 3 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
