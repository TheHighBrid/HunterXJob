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


def test_salary_currency_rate_scaling_and_negation_hardening():
    # Legitimate cross-representation matches
    assert checks._salary_option_contains("$60,000 - $70,000", "CAD 65,000")
    assert checks._salary_option_contains("$60,000 - $70,000", "USD 65,000")
    assert checks._salary_option_contains("CA$60,000 - CA$70,000", "C$65,000")
    assert checks._salary_option_contains("GBP 60,000 - GBP 70,000", "GBP 65,000")
    assert checks._salary_option_contains("£60,000 - £70,000", "GBP 65,000")
    assert checks._salary_option_contains("$60 - $70k", "65,000")
    assert checks._salary_option_contains("$60,000.00 - $70,000.00", "65,000")
    assert checks._salary_option_contains("1000k", "1,000,000")
    assert checks._salary_option_contains("1000k+", "1000000")

    # Currency mismatches
    assert not checks._salary_option_contains("GBP 60,000 - GBP 70,000", "CAD 65,000")
    assert not checks._salary_option_contains("EUR 60,000 - EUR 70,000", "USD 65,000")
    assert not checks._salary_option_contains("£60,000 - £70,000", "$65,000")
    assert not checks._salary_option_contains("€60,000 - €70,000", "$65,000")
    assert not checks._salary_option_contains("£60,000 - £70,000", "65,000")
    assert not checks._salary_option_contains("USD 60,000 - USD 70,000", "CAD 65,000")
    assert not checks._salary_option_contains("AUD 60,000 - AUD 70,000", "$65,000")
    assert not checks._salary_option_contains("CAD 60,000 - USD 70,000", "CAD 65,000")

    # Rate suffixes (including plural, article, biweekly, and small unscaled targets)
    assert not checks._salary_option_contains("$60 - $70/hrs", "65,000")
    assert not checks._salary_option_contains("$60 - $70/hours", "65,000")
    assert not checks._salary_option_contains("$60 - $70 an hour", "65,000")
    assert not checks._salary_option_contains("$60 - $70 a week", "65,000")
    assert not checks._salary_option_contains("$2,000 - $3,000 biweekly", "2,500")
    assert not checks._salary_option_contains("$2,000 - $3,000 semi-monthly", "2,500")
    assert not checks._salary_option_contains("$60/wk", "60")

    # Bare <1000 without k, currency decimals, asymmetrical brackets, and dot-thousands
    assert not checks._salary_option_contains("$60 - $70", "65,000")
    assert not checks._salary_option_contains("$60.00 - $70.00", "65,000")
    assert not checks._salary_option_contains("$60.00 - $70k", "65,000")
    assert not checks._salary_option_contains("$60 - $70,000", "500")
    assert not checks._salary_option_contains("$65+", "500,000")
    assert not checks._salary_option_contains("Up to $70", "65,000")
    assert not checks._salary_option_contains("60.000 - 70.000", "65,000")

    # Negations and negative bounds
    assert not checks._salary_option_contains("Not $65,000", "65,000")
    assert not checks._salary_option_contains("Other than $60,000 - $70,000", "65,000")
    assert not checks._salary_option_contains("Except $65,000", "65,000")
    assert not checks._salary_option_contains("Excluding $60,000 - $70,000", "65,000")
    assert not checks._salary_option_contains("From -60,000 to 70,000", "65,000")


def test_time_zone_rejects_non_na_pacific_asia_negations_and_place_names():
    eastern = "Eastern Time (US & Canada)"
    central = "Central Time (US & Canada)"
    mountain = "Mountain Time (US & Canada)"
    pacific = "Pacific Time (US & Canada)"
    atlantic = "Atlantic Time (Canada)"

    # Legitimate forms still match
    assert checks._same_time_zone("Eastern", eastern)
    assert checks._same_time_zone("US/Eastern", eastern)
    assert checks._same_time_zone("Canada/Eastern", eastern)
    assert checks._same_time_zone("America/St_Johns", "Newfoundland Time")

    # All Pacific/* IANA identifiers are non-North-American-mainland-Pacific
    for bad_pacific in ("Pacific/Easter", "Pacific/Galapagos", "Pacific/Pitcairn", "Pacific/Kiritimati"):
        assert not checks._same_time_zone(bad_pacific, pacific), bad_pacific

    # Non-NA regional names without slash
    assert not checks._same_time_zone("Eastern Asia", eastern)
    assert not checks._same_time_zone("Central Asia", central)
    assert not checks._same_time_zone("Eastern Siberia", eastern)
    assert not checks._same_time_zone("Middle Eastern Time", eastern)

    # Negations other than 'not'
    assert not checks._same_time_zone("Non-Eastern Time", eastern)
    assert not checks._same_time_zone("Except Eastern Time", eastern)
    assert not checks._same_time_zone("Outside Eastern Time", eastern)
    assert not checks._same_time_zone("Excluding Eastern Time", eastern)

    # City/landmark place-name false positives
    assert not checks._same_time_zone("Mountain View, CA", mountain)
    assert not checks._same_time_zone("Central Park", central)
    assert not checks._same_time_zone("Pacific Palisades", pacific)
    assert not checks._same_time_zone("Atlantic City, NJ", atlantic)
    assert not checks._same_time_zone("Atlantic Ocean", atlantic)


def test_yes_no_rejects_disjunctions_unpunctuated_conditionals_and_contradictions():
    assert checks._value_matches_resolved(_item("work_authorization_ca", "Yes, I am open to it", "Yes"))
    assert checks._value_matches_resolved(_item("sponsorship_ca", "No, I do not require sponsorship", "No"))
    assert checks._value_matches_resolved(_item("language_proficiency", "NO, I am not bilingual.", "No"))

    for bad_yes in (
        "Yes/No",
        "Yes / No",
        "Yes or No",
        "Yes with visa sponsorship",
        "Yes (requires sponsorship)",
        "Yes (visa required)",
        "Yes, on condition of sponsorship",
        "Yes, after 6 months",
        "Yes, I am NOT authorized",
    ):
        assert not checks._value_matches_resolved(_item("work_authorization_ca", bad_yes, "Yes")), bad_yes
        problems = checks.planned_answer_problems(
            [_item("work_authorization_ca", bad_yes, "Yes", label="Work auth")],
            {"work_authorization_ca": ["Yes"]},
            False,
            owner_answers={"work_authorization_ca": ["Yes"]},
        )
        assert problems, bad_yes

    for bad_no in (
        "No/Yes",
        "No / Yes",
        "No or Yes",
        "No, I am authorized",
        "No preference",
        "No comment",
        "No (requires sponsorship)",
    ):
        assert not checks._value_matches_resolved(_item("work_authorization_ca", bad_no, "No")), bad_no


def test_language_proficiency_uses_word_boundary_and_rejects_negations():
    resolved = "English (Native)|French (B2)"
    assert checks._value_matches_resolved(_item("language_proficiency", "B2", resolved))
    assert checks._value_matches_resolved(_item("language_proficiency", "French (B2)", resolved))
    assert checks._value_matches_resolved(_item("language_proficiency", "B2 - Upper Intermediate", resolved))
    assert checks._value_matches_resolved(_item("language_proficiency", "B2: Upper intermediate", resolved))
    assert checks._value_matches_resolved(_item("language_proficiency", "B2: Professional working proficiency", resolved))
    assert checks._value_matches_resolved(_item("language_proficiency", "A2: Elementary", "English (Native)|French (A2)"))
    assert checks._cefr_option_matches("a1: no proficiency", "a1")
    assert checks._value_matches_resolved(_item("language_proficiency", "C2: Bilingual/Native speaker", "English (Native)|French (C2)"))
    assert checks._value_matches_resolved(
        _item("language_proficiency", "YES, I am fluent in both French and English.", resolved)
    )
    assert checks._value_matches_resolved(_item("language_proficiency", "Yes / Oui", resolved))
    assert not checks._value_matches_resolved(
        _item("language_proficiency", "YES, I am fluent in both French and English.", "English (Native)|French (B1)")
    )

    for bad_lang in (
        "Not B2",
        "Below B2",
        "Under B2",
        "Less than B2",
        "B20",
        "I speak A1, not B2",
        "B1 or B2",
        "No B2",
        "No, B2",
        "< B2",
        "<= B2",
        "Max B2",
        "Up to B2",
        "Never B2",
        "Other than B2",
        "Exclude B2",
        "Without B2",
        "Lacks B2",
        "B2 / C1",
        "B2-C1",
        "B2+",
        "B2?",
        "French (< B2)",
        "French (Not B2)",
        "B2: No proficiency",
        "Spanish (B2)",
    ):
        assert not checks._value_matches_resolved(_item("language_proficiency", bad_lang, resolved)), bad_lang
    assert not checks._value_matches_resolved(
        {"status": "fill", "key": "q", "label": "Bilingual", "value": ["B1", "B2"],
         "resolved_key": "language_proficiency", "resolved_value": resolved}
    )


def test_yes_no_positive_allowlist_and_challenger_iter3_vectors():
    for good_yes in (
        "Yes",
        "Yes / Oui",
        "Yes, I am authorized",
        "Yes, I am open to it",
        "Yes, I am legally authorized to work in Canada without restriction",
        "Yes, I consent to be recorded",
        "YES, I am fluent in both French and English.",
        "Oui, j'accepte les conditions",
        "Oui - Citoyen canadien ou r\u00e9sident permanent",
        "Yes, located in Canada",
        "Yes, available as required",
        "Yes, I am a Canadian citizen or Permanent Resident",
    ):
        assert checks._value_matches_resolved(_item("work_authorization_ca", good_yes, "Yes")), good_yes

    for bad_yes in (
        "Yes, employer must sponsor",
        "Yes (sponsoring needed)",
        "Yes, without work authorization",
        "Yes, lacking authorization",
        "Yes, I need a work permit",
        "Yes, it depends",
        "Yes, N/A",
        "Yes / N/A",
        "Yes, false",
        "Yes, pending approval",
        "Yes, for 6 months",
        "Yes, until graduation",
        "Yes - Intern",
        "Yes, I require sponsorship now or in the future",
    ):
        assert not checks._value_matches_resolved(_item("work_authorization_ca", bad_yes, "Yes")), bad_yes
        assert checks.planned_answer_problems(
            [_item("work_authorization_ca", bad_yes, "Yes", label="Work auth")],
            {"work_authorization_ca": ["Yes"]},
            False,
            owner_answers={"work_authorization_ca": ["Yes"]},
        ), bad_yes

    for good_no in (
        "No",
        "No / Non",
        "Non, je refuse",
        "No, I have never.",
        "No, I do not require sponsorship",
        "NO, I am not bilingual.",
        "No, I have not worked for D2L in any capacity",
        "No, I do not have a disability",
        "No, I do not hold either",
    ):
        assert checks._value_matches_resolved(_item("sponsorship_ca", good_no, "No")), good_no

    for bad_no in (
        "No, I cannot work without sponsorship",
        "No, I am not able to work without a visa",
        "No, I am authorized, not needing sponsorship",
        "No / Oui",
        "No - Oui",
        "No | Yes",
        "No, yes",
        "No; yes",
        "No (yes)",
        "No, I need a work permit",
        "No, I hold Canadian citizenship",
        "No, I possess work authorization",
        "No, N/A",
        "No, please explain",
        "No, it depends",
        "No, true",
        "No, I have not worked for Acme without sponsorship",
    ):
        assert not checks._value_matches_resolved(_item("sponsorship_ca", bad_no, "No")), bad_no
        assert checks.planned_answer_problems(
            [_item("sponsorship_ca", bad_no, "No", label="Sponsorship")],
            {"sponsorship_ca": ["No"]},
            False,
            owner_answers={"sponsorship_ca": ["No"]},
        ), bad_no


def test_salary_grammar_and_universal_currency_vectors():
    # Positive annual, Unicode dash, Quebec space-thousands, and +100K
    assert checks._salary_option_contains("$60,000 - $70,000 / year", "65,000")
    assert checks._salary_option_contains("$60,000 - $70,000 CAD/yr", "CAD 65,000")
    assert checks._salary_option_contains("$60,000 - $70,000 annually", "65,000")
    assert checks._salary_option_contains("$60,000 \u2013 $70,000", "65,000")
    assert checks._salary_option_contains("$60,000 \u2014 $70,000", "65,000")
    assert checks._salary_option_contains("$60,000 \u2212 $70,000", "65,000")
    assert checks._salary_option_contains("60 000 $ - 70 000 $", "65 000")
    assert checks._salary_option_contains("+100K", "120,000")

    # Strict > / < boundaries
    assert not checks._salary_option_contains("Over $65,000", "65,000")
    assert not checks._salary_option_contains("Under $65,000", "65,000")

    # Spaced & unspaced country-dollar mismatches
    assert not checks._salary_option_contains("US $60,000 - $70,000", "CAD 65,000")
    assert not checks._salary_option_contains("CA $60,000 - $70,000", "USD 65,000")
    assert not checks._salary_option_contains("AU $60,000 - $70,000", "$65,000")
    assert not checks._salary_option_contains("NZ $60,000 - $70,000", "$65,000")
    assert not checks._salary_option_contains("$60,000 - $70,000 (CAD)", "USD 65,000")
    assert not checks._salary_option_contains("U.S. $60,000 - $70,000", "CAD 65,000")

    # Universal ISO codes & non-$ Unicode currency symbols
    for foreign_opt in (
        "MXN 60,000 - 70,000",
        "60,000 - 70,000 MXN",
        "CNY 60,000 - 70,000",
        "SEK 60,000 - 70,000",
        "CHF 60,000 - 70,000",
        "\u20a960,000 - \u20a970,000",
        "\u20bd60,000 - \u20bd70,000",
        "60KES",
        "60KWD",
        "60,000 - 70,000 kr",
    ):
        for res in ("CAD 65,000", "USD 65,000", "$65,000", "65,000"):
            assert not checks._salary_option_contains(foreign_opt, res), (foreign_opt, res)

    # Non-annual rates & arbitrary wrappers
    for rate_opt, target in (
        ("$2,500 per fortnight", "2,500"),
        ("$5,000 p/w", "5,000"),
        ("$5,000 p/m", "5,000"),
        ("$5,000 every week", "5,000"),
        ("$5,000 every month", "5,000"),
        ("$60,000 / month / year", "60,000"),
        ("$60,000 - $70,000 (per month)", "65,000"),
        ("$65,000 OTE", "65,000"),
        ("Yes ($65,000)", "65,000"),
    ):
        assert not checks._salary_option_contains(rate_opt, target), rate_opt

    # Unicode minus & negations
    for bad_sal in (
        "\u221265,000",
        "\u221260,000 to 70,000",
        "From \u221260,000 to 70,000",
        "$60,000 \u221270,000",
        "$60,000 -70,000",
        "Anything but $65,000",
        "Never $65,000",
        "No $65,000",
        "Outside $60,000 - $70,000",
        "Excludes $65,000",
        "Less $65,000",
    ):
        assert not checks._salary_option_contains(bad_sal, "65,000"), bad_sal
        assert checks.planned_answer_problems(
            [_item("salary_expectation", bad_sal, "65,000", label="Expected salary")],
            {"salary_expectation": ["65,000"]},
            False,
            owner_answers={"salary_expectation": ["65,000"]},
        ), bad_sal


def test_time_zone_us_canada_strict_allowlist_and_challenger_iter3_vectors():
    eastern = "Eastern Time (US & Canada)"
    for good_eastern in (
        "ET",
        "EST/EDT",
        "(UTC-05:00) Eastern Time (US & Canada)",
        "Eastern Time (UTC-05:00 / UTC-04:00)",
        "Eastern Daylight Time (EDT)",
        "GMT-4 :Eastern Daylight Time (EDT)",
        "(UTC-05:00) America/Toronto",
        "America/Toronto (UTC-05:00)",
        "Indiana (East)",
    ):
        assert checks._same_time_zone(good_eastern, eastern), good_eastern

    newfoundland = "Newfoundland Time"
    for good_nfld in (
        "(GMT-03:30) Newfoundland",
        "GMT-3:30 :Newfoundland Standard Time (NST)",
        "UTC-03:30",
        "America/St. John's",
    ):
        assert checks._same_time_zone(good_nfld, newfoundland), good_nfld

    pacific = "Pacific Time (US & Canada)"
    mountain = "Mountain Time (US & Canada)"
    atlantic = "Atlantic Time (Canada)"
    central = "Central Time (US & Canada)"
    for bad_tz, ref in (
        ("Philippine Standard Time (PST)", pacific),
        ("Malaysia Standard Time (MST)", mountain),
        ("Pacific Time (New Zealand)", pacific),
        ("Pacific Time (Hawaii)", pacific),
        ("Pacific Time (Honolulu)", pacific),
        ("Pacific Time (Fiji)", pacific),
        ("Eastern Time (Japan)", eastern),
        ("Eastern Time (Korea)", eastern),
        ("Eastern Time (Russia)", eastern),
        ("Eastern Standard Time (Queensland)", eastern),
        ("Atlantic Standard Time (Saudi)", atlantic),
        ("No Eastern Time", eastern),
        ("Without Eastern Time", eastern),
        ("Anything but Eastern Time", eastern),
        ("Unavailable for Eastern Time", eastern),
        ("GMT-2 :Azores Time (AT)", atlantic),
        ("GMT-11 :Nome Time (NT)", newfoundland),
        ("UTC-03:00", eastern),
        ("GMT-04:00 :Eastern Standard Time (EST)", eastern),
        ("GMT-05:00 :Eastern Daylight Time (EDT)", eastern),
        ("America/Havana", eastern),
        ("America/Mexico_City", central),
        ("America/Toronto (PST)", eastern),
        ("et", eastern),
        ("at", atlantic),
        ("Arizona", mountain),
        ("Saskatchewan", central),
    ):
        assert not checks._same_time_zone(bad_tz, ref), bad_tz
        assert checks.planned_answer_problems(
            [_item("time_zone", bad_tz, ref, label="Time zone")],
            {"time_zone": [ref]},
            False,
            owner_answers={"time_zone": [ref]},
        ), bad_tz
