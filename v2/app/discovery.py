from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Job


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


def clean_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(?:p|li|h\d)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def greenhouse_jobs(token: str, timeout: float = 30.0) -> list[JobRecord]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
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
        ))
    return records


def lever_jobs(company: str, timeout: float = 30.0) -> list[JobRecord]:
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
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
            remote="remote" in location.lower(),
            url=item.get("hostedUrl", ""),
            description=description,
        ))
    return records


def discover_all(settings: Settings) -> list[JobRecord]:
    records: list[JobRecord] = []
    for token in settings._csv(settings.greenhouse_board_tokens):
        try:
            records.extend(greenhouse_jobs(token))
        except Exception:
            continue
    for company in settings._csv(settings.lever_companies):
        try:
            records.extend(lever_jobs(company))
        except Exception:
            continue
    return records


def upsert_jobs(db: Session, records: Iterable[JobRecord]) -> int:
    added = 0
    for record in records:
        existing = db.execute(
            select(Job).where(Job.source == record.source, Job.external_id == record.external_id)
        ).scalar_one_or_none()
        if existing:
            existing.title = record.title
            existing.company = record.company
            existing.location = record.location
            existing.remote = record.remote
            existing.url = record.url
            existing.description = record.description
        else:
            db.add(Job(**record.__dict__))
            added += 1
    db.commit()
    return added
