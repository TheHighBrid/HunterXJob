"""Canonical job identity and cross-board duplicate detection.

Every posting gets a stable canonical id ``<ats>:<board>:<job id>`` (e.g.
``ashby:cohere:7133d205-...``). The same role is often posted on several
boards or for several locations; those copies are *linked* to one primary
posting (a :class:`~app.models.JobLink` row plus ``Job.duplicate_of_id``) and
moved to the ``duplicate`` stage. Nothing is deleted, and the owner can unlink
a false positive.

Matching is deliberately conservative (false negatives are cheap, a wrong
link hides a real job):

* only postings of the same normalized employer are compared;
* titles must agree on seniority/level words and numbers;
* ``exact``: same normalized title and identical normalized description;
* ``fuzzy``: title similarity >= 0.92, titles that differ only by place
  words (e.g. "(France)" vs "(Middle East)"), and description SimHash
  distance <= 6 (both descriptions need at least 40 words);
* ``title_location``: same normalized title and overlapping location when
  neither posting has a usable description.

A link never outlives its primary: when the primary posting is rejected,
withdrawn or closed, its copies are released and gated on their own (a copy
can differ where it matters, e.g. a Canadian location for a role whose US
posting was rejected), then re-grouped among themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationMaterial,
    Job,
    JobLink,
    PipelineEvent,
    PipelineStage,
    ReviewTask,
    SubmissionEvidence,
)
from app.state_machine import INACTIVE_STAGES, can_transition

_WORD_RE = re.compile(r"[a-z0-9]+")
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "ltd", "limited", "llc", "llp", "lp", "corp", "corporation", "co", "company", "plc",
    "gmbh", "ag", "sa", "ulc", "ltee", "lte", "the",
})
_TITLE_ABBREVIATIONS = {
    "sr": "senior", "snr": "senior", "jr": "junior", "mgr": "manager", "eng": "engineer", "engr": "engineer",
    "dev": "developer", "assoc": "associate", "admin": "administrator", "rep": "representative", "ii": "2",
    "iii": "3", "iv": "4", "intl": "international",
}
_LEVEL_WORDS = frozenset({
    "intern", "junior", "senior", "staff", "principal", "lead", "manager", "director", "head", "vp", "chief",
    "associate", "fellow", "apprentice", "coop", "student", "new", "graduate",
})
_PLACE_WORDS = frozenset({"remote", "hybrid", "onsite", "on", "site", "office", "anywhere", "canada", "usa", "us"})
_PROVINCES = {
    "on": "ontario", "qc": "quebec", "bc": "british columbia", "ab": "alberta", "mb": "manitoba", "sk": "saskatchewan",
    "ns": "nova scotia", "nb": "new brunswick", "nl": "newfoundland", "pe": "prince edward island",
}
_REGION_WORDS = frozenset({
    "north", "south", "east", "west", "central", "middle", "america", "americas", "europe", "emea", "apac", "asia",
    "pacific", "latam", "nordics", "dach", "benelux", "uk", "eu", "global", "worldwide", "international",
})
# A primary in one of these stages no longer represents the role for its copies.
RELEASING_STAGES = frozenset({PipelineStage.closed.value, PipelineStage.rejected.value, PipelineStage.withdrawn.value})
MIN_DESCRIPTION_WORDS = 40
FUZZY_TITLE_RATIO = 0.92
FUZZY_MAX_HAMMING = 6


def canonical_id(source: str, board: str | None, external_id: str) -> str:
    source = (source or "").strip().lower()
    board = (board or "").strip().lower()
    external_id = (external_id or "").strip()
    return f"{source}:{board}:{external_id}" if board else f"{source}:{external_id}"


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower().replace("&", " and "))


def normalize_company(name: str) -> str:
    words = [word for word in _words(name) if word not in _LEGAL_SUFFIXES]
    return "".join(words)[:40]


def location_tokens(location: str) -> frozenset[str]:
    words = [_PROVINCES.get(word, word) for word in _words(location)]
    return frozenset(word for word in words if len(word) > 1 or word.isdigit())


def _strip_place_suffix(title: str, location: str) -> str:
    """Drop "(Remote)", "- Toronto, ON" style suffixes that only repeat the location."""
    places = location_tokens(location) | _PLACE_WORDS
    def is_place(segment: str) -> bool:
        words = set(_words(segment))
        return bool(words) and words <= (places | set(_PROVINCES) | set(_PROVINCES.values()))
    title = re.sub(r"\(([^)]*)\)", lambda m: "" if is_place(m.group(1)) else m.group(0), title)
    parts = re.split(r"\s[-\u2013\u2014|]\s", title)
    while len(parts) > 1 and is_place(parts[-1]):
        parts.pop()
    return " - ".join(parts)


def normalize_title(title: str, location: str = "") -> str:
    words = [_TITLE_ABBREVIATIONS.get(word, word) for word in _words(_strip_place_suffix(title or "", location))]
    return " ".join(words)


def _level_signature(title_norm: str) -> tuple[frozenset[str], frozenset[str]]:
    words = set(title_norm.split())
    return frozenset(words & _LEVEL_WORDS), frozenset(word for word in words if word.isdigit())


def normalize_description(text: str) -> list[str]:
    return _words(text)


def description_hash(text: str) -> str | None:
    words = normalize_description(text)
    if len(words) < MIN_DESCRIPTION_WORDS:
        return None
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


def simhash64(text: str) -> str | None:
    """64-bit SimHash over 3-word shingles (hex), or None for short texts."""
    words = normalize_description(text)
    if len(words) < MIN_DESCRIPTION_WORDS:
        return None
    weights = [0] * 64
    for index in range(len(words) - 2):
        shingle = " ".join(words[index:index + 3]).encode("utf-8")
        value = int.from_bytes(hashlib.blake2b(shingle, digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if value >> bit & 1 else -1
    fingerprint = sum(1 << bit for bit in range(64) if weights[bit] > 0)
    return f"{fingerprint:016x}"


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def fingerprint(job: Job) -> None:
    """(Re)compute the dedup fingerprints stored on a job row."""
    job.dedup_key = normalize_company(job.company or "") or None
    job.description_hash = description_hash(job.description or "")
    job.description_simhash = simhash64(job.description or "")


@dataclass(frozen=True, slots=True)
class Match:
    method: str
    score: float
    detail: dict[str, Any]


def compare(a: Job, b: Job) -> Match | None:
    """Do two postings describe the same role? (Same employer is checked by the caller.)"""
    detail = _comparable_titles(a, b)
    if detail is None:
        return None
    same_title = detail["title_a"] == detail["title_b"]
    if same_title and _same_description(a, b):
        return Match("exact", 1.0, detail)
    if a.description_simhash and b.description_simhash:
        return _fuzzy(a, b, detail)
    if same_title and not (a.description_hash or b.description_hash):
        return _title_location(a, b, detail)
    return None


def _comparable_titles(a: Job, b: Job) -> dict[str, Any] | None:
    """Normalized titles when both exist and carry the same level/number signature."""
    title_a = normalize_title(a.title or "", a.location or "")
    title_b = normalize_title(b.title or "", b.location or "")
    if not (title_a and title_b) or _level_signature(title_a) != _level_signature(title_b):
        return None
    return {"title_a": title_a, "title_b": title_b}


def _same_description(a: Job, b: Job) -> bool:
    return bool(a.description_hash) and a.description_hash == b.description_hash


def _title_difference(a: Job, b: Job, detail: dict[str, Any]) -> list[str]:
    """Title words that differ between the postings and aren't just place names."""
    places = location_tokens(a.location or "") | location_tokens(b.location or "") | _PLACE_WORDS | _REGION_WORDS
    differing = set(detail["title_a"].split()) ^ set(detail["title_b"].split())
    return sorted(differing - places)


def _fuzzy(a: Job, b: Job, detail: dict[str, Any]) -> Match | None:
    """Near-identical titles (differing only by place words) and near-identical descriptions."""
    difference = _title_difference(a, b, detail)
    if difference:
        detail["title_difference"] = difference
        return None
    ratio = SequenceMatcher(None, detail["title_a"], detail["title_b"]).ratio()
    distance = hamming(a.description_simhash or "", b.description_simhash or "")
    detail.update(title_ratio=round(ratio, 3), simhash_distance=distance)
    if ratio >= FUZZY_TITLE_RATIO and distance <= FUZZY_MAX_HAMMING:
        return Match("fuzzy", round((ratio + 1 - distance / 64) / 2, 3), detail)
    return None


def _title_location(a: Job, b: Job, detail: dict[str, Any]) -> Match | None:
    """Same title, no usable description on either side, and an overlapping location."""
    shared = location_tokens(a.location or "") & location_tokens(b.location or "")
    if not shared:
        return None
    detail["shared_location"] = sorted(shared)
    return Match("title_location", 0.9, detail)


_STAGE_RANK = {stage.value: rank for rank, stage in enumerate(PipelineStage)}


def _progress(db: Session, job: Job) -> tuple[int, int, float]:
    """Higher = further along. Ties go to the earliest discovered posting."""
    has_application = db.execute(select(Application.id).where(Application.job_id == job.id)).first() is not None
    discovered = (job.discovered_at or datetime.now(UTC)).replace(tzinfo=None)
    return int(has_application), _STAGE_RANK.get(job.stage, 0), -discovered.timestamp()


def _unlinked_pairs(db: Session, job: Job) -> set[str]:
    rows = db.execute(select(JobLink).where(
        JobLink.status == "unlinked", or_(JobLink.job_id == job.id, JobLink.primary_job_id == job.id)
    )).scalars()
    return {row.primary_job_id if row.job_id == job.id else row.job_id for row in rows}


def find_duplicate(db: Session, job: Job) -> tuple[Job, Match] | None:
    """The best existing primary posting this job duplicates, if any."""
    if not job.dedup_key:
        return None
    excluded = _unlinked_pairs(db, job)
    candidates = db.execute(select(Job).where(
        Job.dedup_key == job.dedup_key,
        Job.id != job.id,
        Job.duplicate_of_id.is_(None),
        Job.stage.not_in(RELEASING_STAGES),
    )).scalars().all()
    best_job: Job | None = None
    best_match: Match | None = None
    for candidate in candidates:
        if candidate.id in excluded:
            continue
        match = compare(job, candidate)
        if match is not None and (best_match is None or match.score > best_match.score):
            best_job, best_match = candidate, match
    return (best_job, best_match) if best_job is not None and best_match is not None else None


def _close_open_tasks(db: Session, job: Job, note: str) -> int:
    tasks = db.execute(select(ReviewTask).where(ReviewTask.job_id == job.id, ReviewTask.status == "open")).scalars().all()
    for task in tasks:
        task.status = "auto_closed"
        task.resolved_at = datetime.now(UTC)
        task.detail = f"{task.detail}\n\nClosed automatically: {note}".strip()
    return len(tasks)


def deactivate(db: Session, job: Job, stage: PipelineStage, message: str, payload: dict[str, Any] | None = None) -> bool:
    """Move a job (and its application) to ``duplicate``/``closed`` and close its open review tasks."""
    if not can_transition(job.stage, stage.value):
        return False
    previous = job.stage
    job.stage = stage.value
    db.add(PipelineEvent(job_id=job.id, from_stage=previous, to_stage=stage.value, message=message,
                         payload_json=json.dumps(payload) if payload else None))
    application = db.execute(select(Application).where(Application.job_id == job.id)).scalar_one_or_none()
    if application is not None and can_transition(application.stage, stage.value):
        application.stage = stage.value
    _close_open_tasks(db, job, message)
    return True


def link_duplicate(db: Session, job: Job, primary: Job, match: Match) -> JobLink:
    """Record ``job`` as a duplicate of ``primary`` (the more advanced/earliest of the two)."""
    if _progress(db, job) > _progress(db, primary):
        job, primary = primary, job
    link = db.execute(select(JobLink).where(JobLink.job_id == job.id, JobLink.primary_job_id == primary.id)).scalar_one_or_none()
    if link is None:
        link = JobLink(job_id=job.id, primary_job_id=primary.id)
        db.add(link)
    link.method, link.score, link.status = match.method, match.score, "linked"
    link.detail_json = json.dumps({**match.detail, "primary_canonical_id": primary.canonical_id})
    job.duplicate_of_id = primary.id
    # Anything that pointed at the demoted posting now points at the primary.
    for child in db.execute(select(Job).where(Job.duplicate_of_id == job.id)).scalars():
        child.duplicate_of_id = primary.id
    deactivated = deactivate(db, job, PipelineStage.duplicate,
                             f"duplicate of {primary.title} at {primary.company} ({primary.canonical_id}); {match.method} match",
                             {"primary_job_id": primary.id, "method": match.method, "score": match.score})
    # An in-flight posting keeps its stage; the link still blocks it from being prepared/dry-run again.
    link.detail_json = json.dumps({**match.detail, "primary_canonical_id": primary.canonical_id, "deactivated": deactivated})
    db.commit()
    return link


def link_if_duplicate(db: Session, job: Job) -> JobLink | None:
    found = find_duplicate(db, job)
    return link_duplicate(db, job, *found) if found else None


def regate(db: Session, job: Job, message: str) -> None:
    """Send an inactive (duplicate/closed) job back to ``discovered`` so it is gated from scratch."""
    if job.stage not in INACTIVE_STAGES:
        return
    db.add(PipelineEvent(job_id=job.id, from_stage=job.stage, to_stage=PipelineStage.discovered.value, message=message))
    job.stage = PipelineStage.discovered.value
    application = db.execute(select(Application).where(Application.job_id == job.id)).scalar_one_or_none()
    if application is not None and application.stage in INACTIVE_STAGES:
        application.stage = PipelineStage.shortlisted.value


def unlink(db: Session, job: Job) -> Job:
    """Owner override: this posting is not a duplicate. It is re-gated from scratch."""
    if job.duplicate_of_id is None:
        raise ValueError("job is not linked as a duplicate")
    for link in db.execute(select(JobLink).where(JobLink.job_id == job.id, JobLink.status == "linked")).scalars():
        link.status = "unlinked"
        link.decided_at = datetime.now(UTC)
    job.duplicate_of_id = None
    if job.stage == PipelineStage.duplicate.value:
        regate(db, job, "owner: not a duplicate; re-gated from scratch")
    db.commit()
    return job


def release_duplicates(db: Session, primary: Job, reason: str) -> list[str]:
    """The primary is out (rejected/withdrawn/closed): gate its copies on their own.

    Each released copy goes back to ``discovered`` (in-flight copies keep their
    stage), then the copies are re-grouped among themselves so the role is
    still prepared and dry-run at most once. Link rows are kept as ``released``.
    """
    if primary.stage not in RELEASING_STAGES:
        return []
    children = db.execute(select(Job).where(Job.duplicate_of_id == primary.id).order_by(Job.discovered_at)).scalars().all()
    now = datetime.now(UTC)
    for child in children:
        for link in db.execute(select(JobLink).where(JobLink.job_id == child.id, JobLink.status == "linked")).scalars():
            link.status, link.decided_at = "released", now
        child.duplicate_of_id = None
        regate(db, child, f"primary posting {primary.canonical_id} was {reason}; gated on its own")
    db.commit()
    for child in children:
        if child.duplicate_of_id is None and child.stage not in INACTIVE_STAGES:
            link_if_duplicate(db, child)
    return [child.id for child in children]


def group_ids(db: Session, job: Job) -> set[str]:
    """The primary posting plus every posting linked to it."""
    primary_id = job.duplicate_of_id or job.id
    ids = {primary_id, job.id}
    ids.update(db.execute(select(Job.id).where(Job.duplicate_of_id == primary_id)).scalars())
    return ids


def duplicate_guard(db: Session, job: Job, action: str) -> str | None:
    """Why ``job`` must not be prepared (``action="prepare"``) or dry-run (``"dry_run"``), if anything.

    A linked duplicate is never prepared or dry-run, and once any posting of
    the group has materials (prepare) or a dry-run (dry_run), no other posting
    of the same role may do it again.
    """
    if job.duplicate_of_id is not None:
        primary = db.get(Job, job.duplicate_of_id)
        name = primary.canonical_id if primary else job.duplicate_of_id
        return f"duplicate of {name}; only the primary posting is prepared or dry-run"
    others = group_ids(db, job) - {job.id}
    if not others:
        return None
    if action == "prepare":
        done = db.execute(select(ApplicationMaterial.job_id).where(ApplicationMaterial.job_id.in_(others))).first()
    else:
        done = db.execute(
            select(Application.job_id).join(SubmissionEvidence, SubmissionEvidence.application_id == Application.id)
            .where(Application.job_id.in_(others), SubmissionEvidence.kind == "dry_run")
        ).first()
    if done:
        return f"a linked posting of the same role ({done[0]}) already did this; refusing to {action.replace('_', '-')} twice"
    return None


def linked_postings(db: Session, job: Job) -> list[dict[str, Any]]:
    """Other postings of the same role, for the job detail view."""
    ids = group_ids(db, job) - {job.id}
    if not ids:
        return []
    links = {link.job_id: link for link in db.execute(select(JobLink).where(
        JobLink.status == "linked", or_(JobLink.job_id.in_(ids | {job.id}))
    )).scalars()}
    rows = db.execute(select(Job).where(Job.id.in_(ids)).order_by(Job.discovered_at)).scalars()
    out = []
    for other in rows:
        link = links.get(other.id) or links.get(job.id)
        out.append({
            "id": other.id, "title": other.title, "company": other.company, "location": other.location,
            "source": other.source, "canonical_id": other.canonical_id, "stage": other.stage, "url": other.url,
            "relation": "primary" if other.id == job.duplicate_of_id else "duplicate",
            "method": link.method if link else None, "score": link.score if link else None,
        })
    return out


def scan(db: Session) -> dict[str, int]:
    """Fingerprint postings stored before dedup existed, then link duplicates (oldest first)."""
    missing = db.execute(select(Job).where(Job.dedup_key.is_(None))).scalars().all()
    for job in missing:
        fingerprint(job)
    db.commit()
    linked = 0
    rows = db.execute(select(Job).where(Job.duplicate_of_id.is_(None), Job.stage.not_in(INACTIVE_STAGES))
                      .order_by(Job.discovered_at)).scalars().all()
    for job in rows:
        if job.duplicate_of_id is None and job.stage not in INACTIVE_STAGES and link_if_duplicate(db, job) is not None:
            linked += 1
    return {"fingerprinted": len(missing), "linked": linked}
