"""Gate checks accept options derived from exactly the stored answer, nothing looser (fake values)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
checks = importlib.import_module("batch_checks")


def _item(key: str, value: str, resolved: str, label: str = "") -> dict:
    return {"status": "fill", "key": "q", "label": label, "value": value, "resolved_key": key, "resolved_value": resolved}


def test_salary_bracket_must_contain_the_stored_figure():
    assert checks._value_matches_resolved(_item("salary_expectation", "50-60K", "51,234"))
    assert checks._value_matches_resolved(_item("salary_expectation", "70K+", "75000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "$60,000 - $70,000", "65,000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "$165,000 - $180,000", "170,000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "$60,000 - $70,000", "65k"))
    assert checks._value_matches_resolved(_item("salary_expectation", "$100,000+", "120,000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "$100,000+", "100,000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "CAD 60,000 - CAD 70,000", "65,000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "CAD65,000", "65,000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "Up to $70,000", "65,000"))
    assert checks._value_matches_resolved(_item("salary_expectation", "$60k - $70,000", "65,000"))

    assert not checks._value_matches_resolved(_item("salary_expectation", "60-70K", "51,234"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "Prefer not to say", "51,234"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "$165,000 - $180,000", "65,000"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "$60,000 - $70,000", "650000"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "$100,000+", "99,999"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "$70,000 - $60,000", "65,000"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "70-60k", "65,000"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "CAD 60,000 - CAD 70,000", "75,000"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "CAD65,000", "70,000"))
    assert not checks._value_matches_resolved(_item("salary_expectation", "Up to $70,000", "75,000"))


def test_salary_bracket_rejects_challenger_bypass_vectors():
    # Mixed-k scaling bypass ($60k - $70,000 must not parse as 60,000 - 70,000,000)
    assert not checks._salary_option_contains("$60k - $70,000", "500,000")
    # Hourly / rate options must not scale up to annual targets
    assert not checks._salary_option_contains("$60 - $70/hr", "65,000")
    assert not checks._salary_option_contains("$60 hourly", "60,000")
    assert not checks._salary_option_contains("60 to 70 per hour", "65,000")
    assert not checks._salary_option_contains("$65/hour", "65,000")
    # Additive base + bonus must not turn into an open-ended upper bound
    assert not checks._salary_option_contains("Base $60,000 + $10,000 bonus", "500,000")
    assert not checks._salary_option_contains("$60,000 + $10,000", "500,000")
    # Resolved value containing a range / multiple distinct numbers must be rejected
    assert not checks._salary_option_contains("$50,000 - $65,000", "65,000 - 75,000")
    assert not checks._salary_option_contains("$50,000 - $65,000", "60,000 to 70,000")
    # N/A or zero options must not match
    assert not checks._salary_option_contains("N/A - 0", "0")


def test_time_zone_option_must_be_the_same_zone_family():
    stored = "Eastern Time (US & Canada)"
    assert checks._value_matches_resolved(_item("time_zone", "Eastern Time", stored))
    assert checks._value_matches_resolved(_item("time_zone", "GMT-5 :Eastern Standard Time (EST)", stored))
    assert checks._value_matches_resolved(_item("time_zone", "EST", stored))
    assert checks._value_matches_resolved(_item("time_zone", "EDT", stored))
    assert checks._value_matches_resolved(_item("time_zone", "UTC-5", stored))
    assert checks._value_matches_resolved(_item("time_zone", "UTC-05:00", stored))
    assert checks._value_matches_resolved(_item("time_zone", "America/New York", stored))
    assert checks._value_matches_resolved(_item("time_zone", "America/Montreal", stored))
    assert checks._value_matches_resolved(_item("time_zone", "EST5EDT", stored))

    assert not checks._value_matches_resolved(_item("time_zone", "GMT+2 :Eastern European Time (EET)", stored))
    assert not checks._value_matches_resolved(_item("time_zone", "GMT-8 :Pacific Standard Time (PST)", stored))
    assert not checks._value_matches_resolved(_item("time_zone", "UTC+05:00 (Pakistan)", stored))
    assert not checks._value_matches_resolved(_item("time_zone", "Australian Eastern Standard Time (AEST)", stored))


def test_time_zone_rejects_challenger_and_symmetric_bypass_vectors():
    eastern = "Eastern Time (US & Canada)"
    assert not checks._same_time_zone("UTC 05:00", eastern)
    assert not checks._same_time_zone("GMT 05", eastern)
    assert not checks._same_time_zone("UTC+05:00", eastern)
    assert not checks._same_time_zone("GMT+5", eastern)
    assert not checks._same_time_zone("Eastern Australia", eastern)
    assert not checks._same_time_zone("Not Eastern Time (Pacific)", eastern)
    assert not checks._same_time_zone("CST (China Standard Time)", "Central Time (US & Canada)")
    assert not checks._same_time_zone("AST (Arabia Standard Time)", "Atlantic Time (Canada)")
    assert not checks._same_time_zone("Pacific/Auckland", "Pacific Time (US & Canada)")
    assert not checks._same_time_zone("Europe/London", eastern)
    assert not checks._same_time_zone("Asia/Tokyo", eastern)
    assert not checks._same_time_zone("Asia/Singapore", eastern)
    assert not checks._same_time_zone("India Standard Time", eastern)
    assert not checks._same_time_zone("Cuba Standard Time", eastern)
    # Symmetric checks on resolved_value
    assert not checks._same_time_zone("Eastern Time", "Central or Eastern Time")
    assert not checks._same_time_zone("GMT-5 :Eastern Standard Time (EST)", "Eastern European Time")
    assert not checks._same_time_zone("GMT-5 :Eastern Standard Time (EST)", "UTC 05:00")
    assert not checks._same_time_zone("GMT-5 :Eastern Standard Time (EST)", "Eastern Australia")


def test_yes_no_matching_rejects_false_prefixes_and_conditionals():
    assert checks._value_matches_resolved(_item("work_authorization_ca", "Yes", "Yes"))
    assert checks._value_matches_resolved(_item("work_authorization_ca", "Yes / Oui", "Yes"))
    assert checks._value_matches_resolved(_item("work_authorization_ca", "No", "No"))
    assert checks._value_matches_resolved(_item("work_authorization_ca", "No / Non", "No"))
    assert checks._value_matches_resolved(_item("work_authorization_ca", "Yes, I am authorized", "Yes"))
    assert checks._value_matches_resolved(_item("work_authorization_ca", "No, I have never.", "No"))

    assert not checks._value_matches_resolved(_item("work_authorization_ca", "Yesterday", "Yes"))
    assert not checks._value_matches_resolved(_item("work_authorization_ca", "Not applicable", "No"))
    assert not checks._value_matches_resolved(_item("work_authorization_ca", "Non-binary", "No"))
    assert not checks._value_matches_resolved(
        _item("work_authorization_ca", "Yes, only with $100,000 relocation bonus", "Yes")
    )
    assert not checks._value_matches_resolved(_item("work_authorization_ca", "Yes, if visa sponsored", "Yes"))
    assert not checks._value_matches_resolved(_item("work_authorization_ca", "No, unless remote", "No"))


def test_salary_topic_still_needs_an_owner_stored_answer():
    item = _item("salary_expectation", "50-60K", "51,234", label="What are your salary expectations?")
    allowed = {"salary_expectation": ["51,234"]}
    problems = checks.planned_answer_problems([item], allowed, False, owner_answers={})
    assert [p["problem"] for p in problems] == ["answered a never-auto-answer topic: salary"]
    assert checks.planned_answer_problems([item], allowed, False, owner_answers=allowed) == []


def test_policy_backed_rejects_mixed_k_and_non_salary_derived_bypasses():
    # Mixed-k salary bypass must fail both value check and policy backing
    mixed_k = _item("salary_expectation", "$60k - $70,000", "500,000", label="Expected salary")
    sal_owner = {"salary_expectation": ["500,000"]}
    sal_problems = checks.planned_answer_problems([mixed_k], sal_owner, False, owner_answers=sal_owner)
    assert sal_problems

    # Non-salary policy topics require exact match against owner stored answer
    reloc_owner = {"willing_to_relocate": ["Yes"]}
    for bad_val in ("Yes, only with $100,000 relocation bonus", "Yesterday", "Yes / Oui"):
        reloc_item = _item("willing_to_relocate", bad_val, "Yes", label="Are you willing to relocate?")
        problems = checks.planned_answer_problems([reloc_item], reloc_owner, False, owner_answers=reloc_owner)
        assert any("relocation" in p["problem"] for p in problems), bad_val

    not_app_owner = {"willing_to_relocate": ["No"]}
    not_app = _item("willing_to_relocate", "Not applicable", "No", label="Are you willing to relocate?")
    assert checks.planned_answer_problems([not_app], not_app_owner, False, owner_answers=not_app_owner)

    # Exact match passes for non-salary policy topic
    exact_reloc = _item("willing_to_relocate", "Yes", "Yes", label="Are you willing to relocate?")
    assert checks.planned_answer_problems([exact_reloc], reloc_owner, False, owner_answers=reloc_owner) == []
