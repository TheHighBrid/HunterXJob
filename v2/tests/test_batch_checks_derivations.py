"""Gate checks accept options derived from exactly the stored answer, nothing looser (fake values)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
checks = importlib.import_module("batch_checks")


def _item(key: str, value: str, resolved: str) -> dict:
    return {"status": "fill", "key": "q", "label": "", "value": value, "resolved_key": key, "resolved_value": resolved}


def test_salary_bracket_must_contain_the_stored_figure():
    assert checks._value_matches_resolved(_item("salary_expectation", "50-60K", "51,234"))
    assert checks._value_matches_resolved(_item("salary_expectation", "70K+", "75000"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "60-70K", "51,234"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "Prefer not to say", "51,234"))


def test_time_zone_option_must_be_the_same_zone_family():
    stored = "Eastern Time (US & Canada)"
    assert checks._value_matches_resolved(_item("time_zone", "GMT-5 :Eastern Standard Time (EST)", stored))
    assert not checks._value_matches_resolved(_item("time_zone", "GMT+2 :Eastern European Time (EET)", stored))
    assert not checks._value_matches_resolved(_item("time_zone", "GMT-8 :Pacific Standard Time (PST)", stored))


def test_salary_topic_still_needs_an_owner_stored_answer():
    item = {**_item("salary_expectation", "50-60K", "51,234"), "label": "What are your salary expectations?"}
    allowed = {"salary_expectation": ["51,234"]}
    problems = checks.planned_answer_problems([item], allowed, False, owner_answers={})
    assert [p["problem"] for p in problems] == ["answered a never-auto-answer topic: salary"]
    assert checks.planned_answer_problems([item], allowed, False, owner_answers=allowed) == []
