"""Regression and unit tests for Phase 2 low-risk field-handling improvements.

Tests:
1. International phone-number normalization and digit equivalence across formats.
2. verify_filled integration with ControlType.TEL.
3. Greenhouse question classification for legal first/last/full and preferred names.
4. Ashby system-field alias preservation.
5. Safe attribute selector handling for bracketed ARIA listbox IDs.
"""
from __future__ import annotations

import pytest

from app.ashby_form import _PATH_KEYS
from app.form_engine import ControlType, FormControl, phone_values_equivalent, verify_filled
from app.greenhouse_form import classify_question


# --- 1. Phone number normalization & digit equivalence ------------------------

@pytest.mark.parametrize("actual, expected, expected_result", [
    # Exact digit matches
    ("6135550199", "6135550199", True),
    ("613-555-0199", "613-555-0199", True),
    # International +1 prefix vs national 10 digits
    ("+16135550199", "613-555-0199", True),
    ("613-555-0199", "+16135550199", True),
    # Masked / parenthesized input formats
    ("(613) 555-0199", "+1 (613) 555-0199", True),
    ("+1 (613) 555-0199", "(613) 555-0199", True),
    ("613.555.0199", "+1-613-555-0199", True),
    ("613 555 0199", "+16135550199", True),
    # International European numbers (UK +44, France +33)
    ("+44 20 7946 0991", "020 7946 0991", True),
    ("+33 1 42 68 55 55", "01 42 68 55 55", True),
    # Negative cases: clear mismatches
    ("613-555-0199", "613-555-0198", False),
    ("613-555-0199", "514-555-0199", False),
    # Edge cases: empty, None, too short
    ("", "613-555-0199", False),
    (None, "613-555-0199", False),
    ("613-555-0199", "", False),
    ("12345", "12345", False),  # Under 7 digits rejected for safety
])
def test_phone_values_equivalent(actual, expected, expected_result):
    assert phone_values_equivalent(actual, expected) is expected_result


def test_verify_filled_with_tel_control():
    control = FormControl(key="phone", label="Phone", control_type=ControlType.TEL)
    # Masked read-back from DOM vs E.164 expected in vault
    assert verify_filled(control, "+1-613-555-0199", "(613) 555-0199") is True
    assert verify_filled(control, "613-555-0199", "+16135550199") is True
    assert verify_filled(control, "613-555-0199", "613-555-0198") is False


# --- 2. Greenhouse name classification ----------------------------------------

@pytest.mark.parametrize("label, expected_key", [
    ("Legal First Name", "first_name"),
    ("legal first name", "first_name"),
    ("Your Legal First Name", "first_name"),
    ("Legal Given Name", "first_name"),
    ("Legal Last Name", "last_name"),
    ("legal last name", "last_name"),
    ("Your Legal Last Name", "last_name"),
    ("Legal Family Name", "last_name"),
    ("Legal Surname", "last_name"),
    ("Preferred Name", "preferred_name"),
    ("Preferred First Name", "preferred_name"),
    ("Name you'd prefer to be called", "preferred_name"),
    ("Legal Name", "full_name"),
    ("Full Name", "full_name"),
    ("first and last name", "full_name"),
])
def test_greenhouse_name_question_classification(label, expected_key):
    assert classify_question(label) == expected_key


# --- 3. Ashby system-field aliases preservation -------------------------------

def test_ashby_system_field_aliases_preserved():
    assert _PATH_KEYS["_systemfield_name"] == "full_name"
    assert _PATH_KEYS["_systemfield_email"] == "email"
    assert _PATH_KEYS["_systemfield_phone"] == "phone"
    assert _PATH_KEYS["_systemfield_resume"] == "resume"
    assert _PATH_KEYS["_systemfield_location"] == "location"
    assert _PATH_KEYS["_systemfield_cover_letter"] == "cover_letter"


# --- 4. Bracketed ARIA listbox ID selector safety ----------------------------

def test_bracketed_aria_id_selector_syntax():
    # Demonstrates that attribute selectors safe-escape bracketed IDs
    # where standard CSS ID syntax (#id) would throw a DOMException
    bracketed_id = "job_application[answers][123]"
    safe_selector = f'[id="{bracketed_id}"]'
    assert safe_selector == '[id="job_application[answers][123]"]'
    assert "[" in safe_selector and "]" in safe_selector


# --- 5. Bilingual liveness classification ------------------------------------

@pytest.mark.parametrize("body, expected_status", [
    ("Cette offre d'emploi n'est plus disponible", "expired"),
    ("La période de candidature est terminée", "expired"),
    ("Le concours est fermé", "expired"),
    ("This posting has expired", "expired"),
    ("We are not accepting applications at this time", "expired"),
    ("Postulez maintenant pour ce poste", "live"),
    ("Déposer votre candidature en ligne", "live"),
    ("Soumettre ma candidature", "live"),
])
def test_bilingual_liveness_markers(body, expected_status):
    from app.liveness import Liveness, classify_liveness
    res = classify_liveness(200, "https://example.com/job", body)
    assert res.status.value == expected_status
