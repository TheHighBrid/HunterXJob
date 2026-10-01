from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dedup import canonical_id, fingerprint, link_if_duplicate
from app.models import Job

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class JobRecord:
    source: str
    external_id: str
    title: str
    company: str
    location: str
    remote: bool
    url: str
    description: str
    board: str = ""

    @property
    def canonical_id(self) -> str:
        return canonical_id(self.source, self.board, self.external_id)


#: Board slugs/tokens as accepted by the public ATS APIs (also enforced on phone edits).
BOARD_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def _slug(value: str) -> str:
    if not BOARD_SLUG_RE.match(value or ""):
        raise ValueError(f"invalid board slug {value!r}")
    return value


def clean_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</(?:p|li|h\d)>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def greenhouse_jobs(token: str, timeout: float = 30.0) -> list[JobRecord]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{_slug(token)}/jobs?content=true"
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    records: list[JobRecord] = []
    for item in response.json().get("jobs", []):
        location = (item.get("location") or {}).get("name", "")
        records.append(JobRecord(
            source="greenhouse",
            external_id=str(item["id"]),
            title=item.get("title", ""),
            company=token,
            location=location,
            remote="remote" in location.lower(),
            url=item.get("absolute_url", ""),
            description=clean_html(item.get("content", "")),
            board=token.lower(),
        ))
    return records


def lever_jobs(company: str, timeout: float = 30.0) -> list[JobRecord]:
    url = f"https://api.lever.co/v0/postings/{_slug(company)}?mode=json"
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    records: list[JobRecord] = []
    for item in response.json():
        categories = item.get("categories") or {}
        location = categories.get("location", "")
        description = "\n".join([
            clean_html(item.get("description", "")),
            clean_html(item.get("additional", "")),
        ]).strip()
        records.append(JobRecord(
            source="lever",
            external_id=str(item.get("id", "")),
            title=item.get("text", ""),
            company=company,
            location=location,
            remote="remote" in location.lower() or str(item.get("workplaceType") or "").lower() == "remote",
            url=item.get("hostedUrl", ""),
            description=description,
            board=company.lower(),
        ))
    return records


ASHBY_BOARD_API = "https://api.ashbyhq.com/posting-api/job-board/{org}"


def _ashby_location(item: dict[str, Any]) -> str:
    names = [str(item.get("location") or "").strip()]
    names += [str(extra.get("location") or "").strip() for extra in item.get("secondaryLocations") or [] if isinstance(extra, dict)]
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return "; ".join(unique)


def ashby_record(org: str, item: dict[str, Any]) -> JobRecord | None:
    """Normalize one posting from Ashby's public job-board API (None for unlisted postings)."""
    if item.get("isListed") is False or not item.get("id"):
        return None
    location = _ashby_location(item)
    remote = bool(item.get("isRemote")) or str(item.get("workplaceType") or "").lower() == "remote" or "remote" in location.lower()
    description = str(item.get("descriptionPlain") or "").strip() or clean_html(str(item.get("descriptionHtml") or ""))
    return JobRecord(
        source="ashby",
        external_id=str(item["id"]),
        title=str(item.get("title") or "").strip(),
        company=org,
        location=location,
        remote=remote,
        url=str(item.get("jobUrl") or f"https://jobs.ashbyhq.com/{org}/{item['id']}"),
        description=description,
        board=org.lower(),
    )


def ashby_jobs(org: str, timeout: float = 30.0) -> list[JobRecord]:
    """Read-only GET of an organization's public Ashby job board."""
    url = ASHBY_BOARD_API.format(org=_slug(org))
    response = httpx.get(url, params={"includeCompensation": "false"}, timeout=timeout, follow_redirects=False,
                         headers={"Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Ashby job board response has no jobs list")
    records = [ashby_record(org, item) for item in payload["jobs"] if isinstance(item, dict)]
    return [record for record in records if record is not None]


#: A failing board must not stop discovery of the others.
SOURCE_FAILURES: tuple[type[Exception], ...] = (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError)


@dataclass(slots=True)
class DiscoveryRun:
    """Postings found plus which boards were fetched completely (for feed-absence hints)."""

    records: list[JobRecord] = field(default_factory=list)
    complete_boards: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def configured_sources(settings: Settings) -> list[tuple[str, str]]:
    sources = [("greenhouse", token) for token in settings.greenhouse_board_list]
    sources += [("lever", company) for company in settings.lever_company_list]
    sources += [("ashby", org) for org in settings.ashby_org_list]
    return sources


_FETCHERS = {"greenhouse": greenhouse_jobs, "lever": lever_jobs, "ashby": ashby_jobs}


def discover_run(settings: Settings) -> DiscoveryRun:
    """Fetch postings from every configured board. A failing board never stops the others."""
    run = DiscoveryRun()
    for source, board in configured_sources(settings):
        try:
            found = _FETCHERS[source](board)
        except SOURCE_FAILURES as exc:
            message = f"{source}:{board}: {type(exc).__name__}: {exc}"
            logger.warning("discovery failed for %s", message)
            run.errors.append(message)
            continue
        run.records.extend(found)
        run.complete_boards.append((source, board.lower()))
    return run


def discover_all(settings: Settings, errors: list[str] | None = None) -> list[JobRecord]:
    """Fetch postings from every configured board.

    Per-board failures are logged and, when ``errors`` is given, appended to it
    as ``"<source>:<board>: <error>"`` so callers can record them.
    """
    run = discover_run(settings)
    if errors is not None:
        errors.extend(run.errors)
    return run.records


@dataclass(slots=True)
class UpsertResult:
    added: int = 0
    updated: int = 0
    duplicates: int = 0


def _find_existing(db: Session, record: JobRecord) -> Job | None:
    found = db.execute(select(Job).where(Job.canonical_id == record.canonical_id)).scalar_one_or_none()
    if found is not None:
        return found
    return db.execute(
        select(Job).where(Job.source == record.source, Job.external_id == record.external_id)
    ).scalar_one_or_none()


def upsert_records(db: Session, records: Iterable[JobRecord]) -> UpsertResult:
    """Insert new postings (linking duplicates of already-known roles) and refresh known ones."""
    result = UpsertResult()
    now = datetime.now(UTC)
    for record in records:
        existing = _find_existing(db, record)
        if existing:
            description_changed = existing.description != record.description
            for name, value in asdict(record).items():
                setattr(existing, name, value)
            existing.canonical_id = record.canonical_id
            existing.last_seen_at = now
            if description_changed or existing.dedup_key is None:
                fingerprint(existing)
            result.updated += 1
            continue
        job = Job(**asdict(record), canonical_id=record.canonical_id, last_seen_at=now, liveness_failures=0)
        fingerprint(job)
        db.add(job)
        db.flush()
        result.added += 1
        if link_if_duplicate(db, job) is not None:
            result.duplicates += 1
    db.commit()
    return result


def upsert_jobs(db: Session, records: Iterable[JobRecord]) -> int:
    return upsert_records(db, records).added
