"""Offline importer for an export of real applications (ledger import v1).

Reads JSON Lines, validates each record against the schema in
``docs/LEDGER_IMPORT.md`` and labels it with the roadmap's evidence labels.
It never touches the database, the network or a browser; it only reports.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from app.evidence_reconciliation import (
    RELIABILITY_WEIGHTS,
    EvidenceSource,
    EvidenceType,
    OutcomeClassification,
)

SCHEMA_VERSION = "hx-ledger-import-v1"

PRODUCTION_EVIDENCED = "production-evidenced"
DOCUMENTED_UNVERIFIED = "documented but unverified"

# An evidence item must reach this reliability to back a production claim.
MIN_EVIDENCE_WEIGHT = 0.85

REQUIRED_FIELDS = (
    "schema_version",
    "record_id",
    "source_system",
    "company",
    "job_title",
    "job_url",
    "submitted_at",
    "outcome",
    "evidence",
)
OPTIONAL_FIELDS = ("ats", "human_handoff", "notes")
ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)
HANDOFF_REASONS = frozenset({"captcha", "ambiguous_question", "login", "upload", "other"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Fields that would carry the applicant's personal data. The export must not
# contain them; a record that does is rejected, not silently stripped.
PERSONAL_DATA_FIELDS = frozenset(
    {
        "name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "address",
        "salary",
        "salary_expectation",
        "resume",
        "cover_letter",
        "answers",
        "date_of_birth",
        "sin",
        "ssn",
    }
)


@dataclass
class ImportedRecord:
    record_id: str
    company: str
    job_title: str
    outcome: str
    label: str
    evidence_refs: list[str]


@dataclass
class RejectedRecord:
    line: int
    record_id: str
    errors: list[str]


@dataclass
class ImportReport:
    accepted: list[ImportedRecord] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        labels: dict[str, int] = {PRODUCTION_EVIDENCED: 0, DOCUMENTED_UNVERIFIED: 0}
        for record in self.accepted:
            labels[record.label] += 1
        return {
            "schema_version": SCHEMA_VERSION,
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "labels": labels,
            "rejections": [{"line": r.line, "record_id": r.record_id, "errors": r.errors} for r in self.rejected],
        }


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_evidence(items: Any, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        errors.append("evidence must be a list")
        return []
    valid: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        where = f"evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where} must be an object")
            continue
        try:
            kind = EvidenceType(item.get("type"))
        except ValueError:
            errors.append(f"{where}.type is not a known evidence type")
            continue
        try:
            source = EvidenceSource(item.get("source"))
        except ValueError:
            errors.append(f"{where}.source is not a known evidence source")
            continue
        if kind is EvidenceType.DRY_RUN_LEDGER:
            errors.append(f"{where}: a dry-run ledger entry is not evidence of a submission")
            continue
        if not _is_text(item.get("ref")):
            errors.append(f"{where}.ref (pointer to the evidence) is required")
            continue
        digest = item.get("sha256")
        if digest is not None and not (isinstance(digest, str) and SHA256_RE.match(digest)):
            errors.append(f"{where}.sha256 must be 64 lowercase hex characters")
            continue
        valid.append({"type": kind, "source": source, "ref": item["ref"].strip()})
    return valid


def validate_record(record: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (errors, valid evidence items) for one decoded record."""
    if not isinstance(record, dict):
        return ["record must be a JSON object"], []
    errors: list[str] = []
    keys = set(record)
    personal = sorted(keys & PERSONAL_DATA_FIELDS)
    if personal:
        errors.append("personal data fields are not allowed: " + ", ".join(personal))
    unknown = sorted(keys - ALLOWED_FIELDS - PERSONAL_DATA_FIELDS)
    if unknown:
        errors.append("unknown fields: " + ", ".join(unknown))
    missing = [name for name in REQUIRED_FIELDS if name not in record]
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if record.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for name in ("record_id", "source_system", "company", "job_title"):
        if name in record and not _is_text(record[name]):
            errors.append(f"{name} must be a non-empty string")
    if "job_url" in record:
        parsed = urlparse(str(record["job_url"]))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("job_url must be an http(s) URL")
    if "submitted_at" in record:
        try:
            stamp = datetime.fromisoformat(str(record["submitted_at"]))
            if stamp.tzinfo is None:
                errors.append("submitted_at must include a timezone offset")
        except ValueError:
            errors.append("submitted_at must be an ISO 8601 timestamp")
    if "outcome" in record:
        try:
            OutcomeClassification(record["outcome"])
        except ValueError:
            errors.append("outcome is not a known outcome classification")
    handoff = record.get("human_handoff", [])
    if not isinstance(handoff, list) or not set(handoff) <= HANDOFF_REASONS:
        errors.append("human_handoff must be a list of: " + ", ".join(sorted(HANDOFF_REASONS)))
    evidence = _check_evidence(record.get("evidence", []), errors)
    return errors, evidence


def label_for(outcome: str, evidence: list[dict[str, Any]]) -> str:
    """Production-evidenced only for a confirmed submission with strong evidence."""
    if outcome != OutcomeClassification.CONFIRMED_SUBMISSION.value:
        return DOCUMENTED_UNVERIFIED
    for item in evidence:
        weight = RELIABILITY_WEIGHTS.get((item["type"], item["source"]), 0.0)
        if weight >= MIN_EVIDENCE_WEIGHT:
            return PRODUCTION_EVIDENCED
    return DOCUMENTED_UNVERIFIED


def import_lines(lines: Iterable[str]) -> ImportReport:
    """Validate and label JSON Lines; duplicates by record_id are rejected."""
    report = ImportReport()
    seen: set[str] = set()
    for number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError as exc:
            report.rejected.append(RejectedRecord(number, "", [f"invalid JSON: {exc.msg}"]))
            continue
        record_id = str(record.get("record_id", "")) if isinstance(record, dict) else ""
        errors, evidence = validate_record(record)
        if record_id and record_id in seen:
            errors.append("duplicate record_id")
        if errors:
            report.rejected.append(RejectedRecord(number, record_id, errors))
            continue
        seen.add(record_id)
        report.accepted.append(
            ImportedRecord(
                record_id=record_id,
                company=record["company"].strip(),
                job_title=record["job_title"].strip(),
                outcome=record["outcome"],
                label=label_for(record["outcome"], evidence),
                evidence_refs=[item["ref"] for item in evidence],
            )
        )
    return report
