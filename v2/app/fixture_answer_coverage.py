"""Offline answer-matching coverage across sanitized ATS fixtures.

Measures how many form controls ``plan_fill`` marks fill / review / skip when
the vault is loaded from the example verified profile plus explicit policy
answers for the classifier keys (start date, background-check consent, etc.).

Fixtures only. No network. No live employer hits.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ashby_form import AshbyJobRef, parse_ashby_payload
from app.db import Base
from app.flags import ensure_flags
from app.form_engine import ControlType, plan_fill
from app.greenhouse_form import GreenhouseJobRef, parse_greenhouse_payload
from app.lever_form import LeverJobRef, parse_lever_apply_page
from app.profile import import_profile_document
from app.vault_store import load_vault, upsert_answer

ROOT = Path(__file__).resolve().parents[1]

FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLE_PROFILE = ROOT / "examples" / "profile.example.yaml"

# Explicit policy answers for classifier keys the example profile does not own.
# Values are chosen to match fixture option labels where known; otherwise a
# simple Yes / date string so resolve succeeds and option matching decides.
POLICY_ANSWERS = (
    ("availability_date", "Two weeks", "policy"),
    ("background_check_consent", "Yes", "policy"),
    ("employment_status", "Currently employed", "user"),
    ("education_level", "Bachelor's Degree", "user"),
    ("ottawa_commute", "Yes", "user"),
    ("citizenship_ca", "Yes", "policy"),
    ("voluntary_self_identification", "decline", "policy"),
    ("linkedin_url", "https://www.linkedin.com/in/avery-quinn-example", "user"),
    ("salary_expectation", "70K+", "user"),
    ("willing_to_relocate", "I am already living near this location", "user"),
    ("referral_source", "Other", "user"),
    ("time_zone", "GMT-5 :Eastern Standard Time (EST)", "user"),
    ("country_of_residence", "Canada", "user"),
)


@dataclass(frozen=True)
class FormSummary:
    name: str
    platform: str
    controls: int
    fill: int
    review: int
    skip: int

    @property
    def planned_pct(self) -> float:
        total = self.fill + self.review + self.skip
        return round(100.0 * self.fill / total, 1) if total else 0.0


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ensure_flags(db)
    return db


def _seed_vault(db):
    import yaml

    document = yaml.safe_load(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    document["verified"] = True
    import_profile_document(db, document, origin="profile.example.yaml")
    for key, value, source in POLICY_ANSWERS:
        upsert_answer(db, key=key, value=value, source=source, sensitive=key.endswith("consent") or key.startswith("citizenship"))
    return load_vault(db)


def _summarize(name: str, platform: str, controls, vault) -> FormSummary:
    visible = [c for c in controls if c.control_type is not ControlType.HIDDEN]
    plan = plan_fill(visible, vault)
    counts = Counter(item.status for item in plan.items)
    return FormSummary(
        name=name,
        platform=platform,
        controls=len(visible),
        fill=counts.get("fill", 0),
        review=counts.get("review", 0),
        skip=counts.get("skip", 0),
    )


def fixture_forms() -> list[tuple[str, str, list]]:
    """(name, platform, controls) for every sanitized fixture form, parsed offline."""
    forms: list[tuple[str, str, list]] = []
    for rel, ref in (
        ("greenhouse/asana_8165477.json", GreenhouseJobRef("asana", "8165477")),
        ("greenhouse/d2l_7696196.json", GreenhouseJobRef("d2l", "7696196")),
        ("greenhouse/gitlab_8556658002.json", GreenhouseJobRef("gitlab", "8556658002")),
    ):
        payload = json.loads((FIXTURES / rel).read_text(encoding="utf-8"))
        forms.append((rel, "greenhouse", parse_greenhouse_payload(payload, ref).controls))

    lever_html = (FIXTURES / "ats" / "lever_apply.html").read_text(encoding="utf-8")
    lever = parse_lever_apply_page(lever_html, LeverJobRef("northwind", "ed663b5f-1b13-5fb7-853b-e4cdf805c6dd"), job_location="London")
    forms.append(("ats/lever_apply.html", "lever", lever.controls))

    for name, ref in (
        ("ashby_form_advisor.json", AshbyJobRef("northwind", "9b99caed-e385-574f-94f7-4be75f67782d")),
        ("ashby_form_engineer.json", AshbyJobRef("northwind", "831c138d-1ab7-5afa-b938-dd5da8882214")),
    ):
        payload = json.loads((FIXTURES / "ats" / name).read_text(encoding="utf-8"))
        form = parse_ashby_payload(payload["data"], ref, job_location="Toronto, Ontario")
        forms.append((f"ats/{name}", "ashby", form.controls))
    return forms


def seeded_vault():
    """Vault from the example profile plus the explicit policy answers above."""
    return _seed_vault(_db())


def collect() -> list[FormSummary]:
    vault = seeded_vault()
    return [_summarize(name, platform, controls, vault) for name, platform, controls in fixture_forms()]


def totals(rows: list[FormSummary]) -> dict[str, int | float]:
    fill = sum(row.fill for row in rows)
    review = sum(row.review for row in rows)
    skip = sum(row.skip for row in rows)
    controls = sum(row.controls for row in rows)
    planned = fill + review + skip
    return {
        "forms": len(rows),
        "controls": controls,
        "fill": fill,
        "review": review,
        "skip": skip,
        "planned_pct": round(100.0 * fill / planned, 1) if planned else 0.0,
    }


def report() -> dict:
    rows = collect()
    return {"forms": [asdict(row) | {"planned_pct": row.planned_pct} for row in rows], "totals": totals(rows)}


def main(out: Path | None = None) -> int:
    data = report()
    print(json.dumps(data, indent=2))
    target = out or ROOT / "reports" / "fixture_answer_coverage.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
