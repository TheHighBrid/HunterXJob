"""Read-only, opt-in Context.dev fallback for public job descriptions.

Never authorize applications or classify jobs as closed from scrape results.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

API_URL = "https://api.context.dev/v1/web/scrape"
FORMATS = ("html", "markdown", "screenshot", "images", "bytes", "parse", "json", "highlights", "product")
logger = logging.getLogger(__name__)

SCALARS = ("job_title", "employer", "location", "work_mode", "employment_type", "salary",
           "currency", "language_requirements", "posting_date", "closing_date", "application_url")
JOB_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        **{field: {"type": ["string", "null"]} for field in SCALARS},
        "responsibilities": {"type": "array", "items": {"type": "string"}},
        "qualifications": {"type": "array", "items": {"type": "string"}},
        "source_excerpts": {"type": "object", "additionalProperties": False,
                            "properties": {key: {"type": ["string", "null"]} for key in
                                           ("salary", "remote_eligibility", "mandatory_requirements")}},
    },
}
JOB_INSTRUCTIONS = (
    "Extract only explicitly published facts. Capture short verbatim excerpts supporting salary, "
    "remote eligibility, and mandatory requirements. Missing scalars must be null; missing lists []. "
    "Do not guess dates, salary, location or remote eligibility. A sign-in wall is not an active posting."
)


class ContextDevError(RuntimeError):
    """Non-definitive upstream failure. Never use to mark a job as closed."""


def public_url(url: str) -> str:
    """Guard against accidentally sending internal URLs or credentials to a hosted API."""
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise ValueError("a public HTTP(S) URL without credentials is required")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal", ".test")):
        raise ValueError("private destinations are prohibited")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if "." not in host:
            raise ValueError("a public fully qualified hostname is required")
    else:
        if not ip.is_global:
            raise ValueError("private destinations are prohibited")
    if parts.port not in (None, 80, 443):
        raise ValueError("nonstandard target ports are prohibited")
    return url


def scrape_url(
    url: str, *, formats: dict[str, bool], api_key: str,
    json_schema: dict[str, Any] | None = None, instructions: str | None = None,
    parse_params: dict[str, Any] | None = None, highlights_query: str | None = None,
    highlights_max_characters: int = 2000, dedupe_product_images: bool = False,
    max_age_ms: int = 21_600_000, timeout_seconds: float = 20.0,
    total_budget_seconds: float = 25.0, retries: int = 1, zdr: bool = False,
    country: str | None = None, client: httpx.Client | None = None,
) -> dict[str, Any]:
    """POST /web/scrape; expose requested/success/data for every output independently."""
    public_url(url)
    if not api_key:
        raise ContextDevError("CONTEXT_DEV_API_KEY is not configured")
    requested = {name: True for name, enabled in formats.items() if enabled}
    if not requested or set(requested) - set(FORMATS):
        raise ValueError("select at least one supported format")
    if requested.get("json") and (not isinstance(json_schema, dict) or json_schema.get("type") != "object"):
        raise ValueError("JSON output requires an object JSON Schema")
    if json_schema is not None and len(json.dumps(json_schema)) > 50_000:
        raise ValueError("JSON Schema exceeds 50,000 characters")
    if instructions and len(instructions) > 2000:
        raise ValueError("JSON instructions exceed 2,000 characters")
    if requested.get("parse") and not parse_params:
        raise ValueError("parse output requires parseParams")
    if requested.get("highlights") and (not highlights_query or not 100 <= highlights_max_characters <= 20_000):
        raise ValueError("highlights require a query and valid character limit")
    if requested.get("highlights") and zdr:
        raise ValueError("highlights cannot be combined with ZDR")
    if not 0 <= max_age_ms <= 31_536_000_000:
        raise ValueError("invalid maxAgeMs")
    body: dict[str, Any] = {
        "url": url, "formats": requested, "maxAgeMs": max_age_ms,
        "timeoutOpts": {"milliseconds": int(timeout_seconds * 1000)},
        "zdr": "enabled" if zdr else "disabled",
    }
    if country:
        if len(country) != 2 or not country.isalpha():
            raise ValueError("country must be two letters")
        body["sharedParams"] = {"country": country.upper()}
    if requested.get("json"):
        body["jsonParams"] = {"schema": json_schema}
        if instructions:
            body["jsonParams"]["instructions"] = instructions
    if requested.get("parse"):
        body["parseParams"] = parse_params
    if requested.get("highlights"):
        body["highlightsParams"] = {"query": highlights_query, "maxCharacters": highlights_max_characters}
    if requested.get("product"):
        body["productParams"] = {"dedupeImages": dedupe_product_images}

    owned = client is None
    http = client or httpx.Client(follow_redirects=False)
    start = time.monotonic()
    try:
        for attempt in range(max(0, min(retries, 1)) + 1):
            remaining = total_budget_seconds - (time.monotonic() - start)
            if remaining <= 0:
                raise ContextDevError("caller time budget exhausted")
            try:
                response = http.post(
                    API_URL, json=body, timeout=min(timeout_seconds, remaining),
                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                )
                if response.status_code in {408, 409, 429, 500, 502, 503, 504} and attempt < retries:
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ContextDevError("Context.dev returned invalid JSON")
                outputs = {}
                for name in FORMATS:
                    item = payload.get("parsed" if name == "parse" else name)
                    if isinstance(item, dict):
                        outputs[name] = {
                            "requested": bool(item.get("requested")),
                            "success": item.get("success"),
                            "data": item.get("data") if item.get("success") is True else None,
                            "error_code": item.get("error_code"), "message": item.get("message"),
                        }
                        if name == "parse":
                            outputs[name]["type"] = item.get("type")
                    else:
                        outputs[name] = {"requested": bool(requested.get(name)),
                                         "success": False if requested.get(name) else None, "data": None}
                return {
                    "source_url": url, "final_url": payload.get("url", url),
                    "retrieved_at": datetime.now(UTC).isoformat(), "request_id": payload.get("request_id"),
                    "is_partial": bool(payload.get("isPartial")) or any(
                        output["success"] is False for output in outputs.values() if output["requested"]
                    ),
                    "outputs": outputs,
                }
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt >= retries:
                    raise ContextDevError(f"transport failure: {type(exc).__name__}") from exc
            except httpx.HTTPStatusError as exc:
                raise ContextDevError(f"HTTP {exc.response.status_code}") from exc
    finally:
        if owned:
            http.close()
    raise ContextDevError("scrape failed")


def enrich_record(record: Any, *, api_key: str, settings: Any, client: httpx.Client | None = None,
                  refresh: bool = False) -> dict[str, Any]:
    """Fill missing description/job fields, never replace verified source facts."""
    result = scrape_url(
        record.url, formats={"json": True, "markdown": True}, api_key=api_key,
        json_schema=JOB_SCHEMA, instructions=JOB_INSTRUCTIONS,
        max_age_ms=0 if refresh else int(settings.context_dev_cache_hours * 3_600_000),
        timeout_seconds=settings.context_dev_timeout_seconds,
        total_budget_seconds=settings.context_dev_budget_seconds, retries=1,
        zdr=settings.context_dev_zdr, client=client,
    )
    item = result["outputs"]["json"]
    values = item["data"] if item["success"] is True and isinstance(item["data"], dict) else {}
    for attribute, source in (("title", "job_title"), ("company", "employer"), ("location", "location")):
        value = values.get(source)
        if not getattr(record, attribute) and isinstance(value, str):
            setattr(record, attribute, value.strip())
    if isinstance(values.get("work_mode"), str) and values["work_mode"].strip().lower() in {"remote", "fully remote"}:
        record.remote = True
    if len(record.description.strip()) < 120:
        lines = [s.strip() for name in ("responsibilities", "qualifications")
                 for s in (values.get(name) if isinstance(values.get(name), list) else [])
                 if isinstance(s, str) and s.strip()]
        md = result["outputs"]["markdown"]
        additional = "\n".join(lines) if lines else (
            md["data"] if md["success"] is True and isinstance(md["data"], str) else ""
        )
        if additional.strip():
            record.description = (record.description.strip() + "\n" + additional.strip()).strip()[:12_000]
    result["extracted"] = values
    return result


def append_audit(result: dict[str, Any], path: str) -> None:
    """Append public metadata and per-format status without secrets or page dumps."""
    dest = Path(path).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    event = {key: result.get(key) for key in ("source_url", "final_url", "retrieved_at", "request_id", "is_partial")}
    event["formats"] = {name: {key: info.get(key) for key in ("requested", "success", "error_code")}
                        for name, info in result["outputs"].items()}
    event["extracted"] = result.get("extracted", {})
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
