"""Group the review/skip controls from the offline fixture coverage run by gap.

Answers "which classifier gaps matter most": every non-fill control from
``fixture_answer_coverage`` is bucketed by control type and by a gap category
(documents, option mismatch, prior employment, legal/consent, ...).

Fixtures only. No network. No live employer hits.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.fixture_answer_coverage import ROOT, fixture_forms, seeded_vault
from app.form_engine import ControlType, FillPlanItem, plan_fill

OPTION_MISMATCH = "resolved value is not a listed option"
DOCUMENT_KEYS = frozenset({"resume", "resume_text", "cover_letter", "cover_letter_text"})
PROFILE_KEYS = frozenset({"preferred_name", "website_url", "github_url", "twitter_url", "urls[Other]", "Other"})
LEGAL_SECTIONS = frozenset({"consent", "data_compliance"})

# (category, label pattern) checked in order after the key-based rules.
LABEL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("legal_consent", re.compile(r"government", re.IGNORECASE)),
    ("work_authorization", re.compile(r"sponsor|visa|authori[sz]ed to work", re.IGNORECASE)),
    ("prior_employment", re.compile(r"(previously|ever)\s+(been\s+)?(employed|worked)|employed, or otherwise engaged|worked (at|for)", re.IGNORECASE)),
    ("sensitive_self_id", re.compile(r"pronoun|accessib|accommodat|exclusive and", re.IGNORECASE)),
    ("legal_consent", re.compile(r"consent|certify|disclaimer|agreement", re.IGNORECASE)),
    ("referral", re.compile(r"referr", re.IGNORECASE)),
    ("profile_names_links", re.compile(r"^(x|twitter|github|portfolio|other website)$", re.IGNORECASE)),
)


def gap_category(item: FillPlanItem) -> str:
    """Bucket one non-fill plan item into the classifier gap it exposes."""
    control = item.control
    key = control.vault_keys[-1] if control.vault_keys else control.key
    if item.reason == OPTION_MISMATCH:
        return "option_mismatch"
    if key in DOCUMENT_KEYS:
        return "documents"
    if key in PROFILE_KEYS:
        return "profile_names_links"
    if getattr(control, "section", "") in LEGAL_SECTIONS:
        return "legal_consent"
    for category, pattern in LABEL_RULES:
        if pattern.search(control.label or ""):
            return category
    if control.sensitive:
        return "sensitive_self_id"
    if control.control_type is ControlType.TEXTAREA:
        return "free_text_essay"
    return "role_screening"


def gap_items() -> list[tuple[str, FillPlanItem]]:
    vault = seeded_vault()
    out: list[tuple[str, FillPlanItem]] = []
    for name, _platform, controls in fixture_forms():
        visible = [c for c in controls if c.control_type is not ControlType.HIDDEN]
        out.extend((name, item) for item in plan_fill(visible, vault).items if item.status != "fill")
    return out


def _bucket(rows: list[tuple[str, str, FillPlanItem]]) -> list[dict]:
    grouped: dict[str, Counter] = defaultdict(Counter)
    for bucket, _name, item in rows:
        grouped[bucket][item.status] += 1
        grouped[bucket]["required"] += int(item.control.required)
    ordered = sorted(grouped.items(), key=lambda kv: (-(kv[1]["review"] + kv[1]["skip"]), kv[0]))
    return [
        {"name": name, "total": c["review"] + c["skip"], "review": c["review"], "skip": c["skip"], "required": c["required"]}
        for name, c in ordered
    ]


def report() -> dict:
    items = gap_items()
    by_category = [(gap_category(item), name, item) for name, item in items]
    by_type = [(item.control.control_type.value, name, item) for name, item in items]
    status = Counter(item.status for _name, item in items)
    return {
        "totals": {"review": status["review"], "skip": status["skip"]},
        "by_category": _bucket(by_category),
        "by_control_type": _bucket(by_type),
        "controls": [
            {"form": name, "status": item.status, "category": cat, "type": item.control.control_type.value,
             "required": item.control.required, "label": (item.control.label or "")[:80]}
            for cat, name, item in by_category
        ],
    }


def main(out: Path | None = None) -> int:
    data = report()
    print(json.dumps({k: data[k] for k in ("totals", "by_category", "by_control_type")}, indent=2))
    target = out or ROOT / "reports" / "fixture_answer_gaps.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
