"""Deterministic option derivation when a resolved answer is not literally an option.

Only narrow, explainable rules; anything else stays in review:

- yes/no answers pick the single option that starts with "Yes" / "No";
- verified ``language_proficiency`` answers bilingual English+French questions:
  a Yes/No question gets "Yes" only when both languages are at professional
  working level or above, and a CEFR-scale question gets the weaker of the two
  languages' levels.
- compensation / salary range questions evaluate candidate target / minimum
  compensation against option brackets (e.g. 60-70K for $65,000 / $60,000);
- time zone questions normalize candidate Eastern Time / America/Toronto / EST
  to platform option formats (e.g. 'GMT-5 :Eastern Standard Time (EST)');
- work location questions match remote preferences for non-local roles
  (e.g. 'Remote Canada' or 'Remote within Canada');
- legal disclaimer and consent declarations resolve to universal affirmative
  options (e.g. 'Yes' or 'I agree').
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.form_engine import FormControl

_YES = frozenset({"yes", "y", "true"})
_NO = frozenset({"no", "n", "false"})
_CEFR_ORDER = ("A1", "A2", "B1", "B2", "C1", "C2")
# Ordered most specific first; proficiency labels are free text from the profile.
_LEVELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"native|bilingual|mother tongue|c2", re.IGNORECASE), "C2"),
    (re.compile(r"full professional|fluent|c1", re.IGNORECASE), "C1"),
    (re.compile(r"professional working|professional|b2", re.IGNORECASE), "B2"),
    (re.compile(r"limited working|intermediate|b1", re.IGNORECASE), "B1"),
    (re.compile(r"elementary|basic|a2", re.IGNORECASE), "A2"),
)
_PROFESSIONAL = "B2"
_CEFR_OPTION_RE = re.compile(r"^\s*([ABC][12])\b")
_LANG_ITEM_RE = re.compile(r"^\s*([^()|]+?)\s*\(([^)]*)\)\s*$")


def _single_prefixed(control: Any, word: str) -> str | None:
    pattern = re.compile(rf"^\s*{word}\b", re.IGNORECASE)
    options = getattr(control, "options", []) or []
    candidates = [option for option in options if pattern.search(option)]
    return candidates[0] if len(candidates) == 1 else None


def _wrap(control: Any, option: str | None) -> Any | None:
    if option is None:
        return None
    ctype = getattr(control, "control_type", None)
    ctype_val = getattr(ctype, "value", str(ctype or ""))
    return [option] if ctype_val == "multiselect" else option


_AGREE_PATTERNS = re.compile(
    r"\b(?:agree|agreed|acknowledge|acknowledged|accept|accepted|certify|consent|yes)\b",
    re.IGNORECASE,
)


def _yes_no(control: Any, value: Any) -> Any | None:
    text = " ".join(str(value).split()).casefold()
    options = getattr(control, "options", []) or []
    if text in _YES:
        opt = _single_prefixed(control, "yes")
        if opt is not None:
            return _wrap(control, opt)
        agree_opts = [option for option in options if _AGREE_PATTERNS.search(option)]
        if len(agree_opts) == 1:
            return _wrap(control, agree_opts[0])
        if len(options) == 1:
            return _wrap(control, options[0])
    if text in _NO:
        return _wrap(control, _single_prefixed(control, "no"))
    return None


def _cefr(level_text: str) -> str | None:
    for pattern, code in _LEVELS:
        if pattern.search(level_text):
            return code
    return None


def parse_languages(value: Any) -> dict[str, str]:
    """``"English (Native)|French (Professional working)"`` -> {"english": "C2", "french": "B2"}."""
    parts = value if isinstance(value, (list, tuple)) else str(value).split("|")
    levels: dict[str, str] = {}
    for part in parts:
        match = _LANG_ITEM_RE.match(str(part))
        if match:
            code = _cefr(match.group(2))
            if code:
                levels[match.group(1).strip().casefold()] = code
    return levels


def _bilingual(control: FormControl, value: Any) -> Any | None:
    label = (control.label or "").casefold()
    if "english" not in label or "french" not in label:
        return None
    levels = parse_languages(value)
    if "english" not in levels or "french" not in levels:
        return None
    weaker = min(levels["english"], levels["french"], key=_CEFR_ORDER.index)
    cefr_options = {m.group(1): option for option in control.options if (m := _CEFR_OPTION_RE.match(option))}
    if cefr_options:
        return _wrap(control, cefr_options.get(weaker))
    if _CEFR_ORDER.index(weaker) >= _CEFR_ORDER.index(_PROFESSIONAL):
        return _wrap(control, _single_prefixed(control, "yes"))
    return None


# ---------------------------------------------------------------------------
# Compensation & Salary Range Normalization
# ---------------------------------------------------------------------------

def _parse_salary_number(s: Any) -> float | None:
    if s is None:
        return None
    cleaned = str(s).strip().lower().replace(",", "").replace("$", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*k\b", cleaned)
    if m:
        return float(m.group(1)) * 1000.0
    m = re.search(r"\b\d+(?:\.\d+)?\b", cleaned)
    if m:
        return float(m.group(0))
    return None


def _parse_salary_bounds(opt: str) -> tuple[float, float] | None:
    text = opt.strip().lower().replace(",", "").replace("$", "")
    # Plus / and above / +100k / 100k+ / over 100k
    plus_m = re.search(
        r"(?:\+|over|above|more than)\s*(\d+(?:\.\d+)?)\s*(k)?|(\d+(?:\.\d+)?)\s*(k)?\s*(?:\+|\band above\b|\bor more\b)",
        text,
    )
    if plus_m:
        num = plus_m.group(1) or plus_m.group(3)
        is_k = bool(plus_m.group(2) or plus_m.group(4))
        val = float(num) * (1000.0 if (is_k or float(num) < 1000) else 1.0)
        return (val, float("inf"))
    # Range "30-40k", "60-70k", "90k-100k", "50000 - 60000", "50000 to 60000"
    range_m = re.search(r"(\d+(?:\.\d+)?)\s*(k)?\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*(k)?", text)
    if range_m:
        low_num = float(range_m.group(1))
        high_num = float(range_m.group(3))
        has_k = bool(range_m.group(2) or range_m.group(4)) or "k" in text
        multiplier = 1000.0 if (has_k or high_num < 1000) else 1.0
        return (low_num * multiplier, high_num * multiplier)
    # Under / less than
    under_m = re.search(r"(?:under|less than|<)\s*(\d+(?:\.\d+)?)\s*(k)?", text)
    if under_m:
        num = float(under_m.group(1))
        has_k = bool(under_m.group(2)) or "k" in text
        multiplier = 1000.0 if (has_k or num < 1000) else 1.0
        return (0.0, num * multiplier)
    # Exact single number option like "$65,000" or "65000"
    single_num = _parse_salary_number(text)
    if single_num is not None:
        return (single_num, single_num)
    return None


def _salary_range(control: FormControl, value: Any) -> Any | None:
    target_salary = _parse_salary_number(value)
    if target_salary is None or not control.options:
        return None

    # Parse bounds for each option
    parsed: list[tuple[str, tuple[float, float]]] = []
    for opt in control.options:
        b = _parse_salary_bounds(opt)
        if b is not None:
            parsed.append((opt, b))

    if not parsed:
        return None

    # Look for exact bracket match (low <= target <= high)
    matches = [opt for opt, (low, high) in parsed if low <= target_salary <= high]
    if len(matches) == 1:
        return _wrap(control, matches[0])
    if len(matches) > 1:
        # If on boundary (e.g. 60,000 matches [50k, 60k] and [60k, 70k]), pick the bracket where it is the lower bound
        upper_tier = [opt for opt, (low, high) in parsed if low == target_salary]
        if len(upper_tier) == 1:
            return _wrap(control, upper_tier[0])
        return _wrap(control, matches[0])

    # If target is above all listed brackets, pick the highest open-ended tier
    above = [opt for opt, (low, high) in parsed if high == float("inf") and target_salary >= low]
    if len(above) == 1:
        return _wrap(control, above[0])

    # Fallback to closest option whose lower bound is acceptable
    viable = [opt for opt, (low, high) in parsed if high >= target_salary]
    if viable:
        viable.sort(key=lambda o: _parse_salary_bounds(o)[0])
        return _wrap(control, viable[0])

    return None


# ---------------------------------------------------------------------------
# Time Zone Normalization
# ---------------------------------------------------------------------------

_TZ_PATTERNS: dict[str, re.Pattern[str]] = {
    "eastern": re.compile(r"eastern|\best\b|\bedt\b|toronto|new_york|gmt[- ]?0?5\b|utc[- ]?0?5\b", re.IGNORECASE),
    "central": re.compile(r"central|\bcst\b|\bcdt\b|chicago|winnipeg|gmt[- ]?0?6\b|utc[- ]?0?6\b", re.IGNORECASE),
    "mountain": re.compile(r"mountain|\bmst\b|\bmdt\b|denver|edmonton|calgary|gmt[- ]?0?7\b|utc[- ]?0?7\b", re.IGNORECASE),
    "pacific": re.compile(r"pacific|\bpst\b|\bpdt\b|vancouver|los_angeles|gmt[- ]?0?8\b|utc[- ]?0?8\b", re.IGNORECASE),
    "atlantic": re.compile(r"atlantic|\bast\b|\badt\b|halifax|gmt[- ]?0?4\b|utc[- ]?0?4\b", re.IGNORECASE),
    "newfoundland": re.compile(r"newfoundland|\bnst\b|\bndt\b|st_johns|gmt[- ]?0?3:?30\b", re.IGNORECASE),
}

_TZ_EXCLUSIONS: dict[str, re.Pattern[str]] = {
    "eastern": re.compile(r"european|australian|arabia|\beet\b|\baest\b|\baedt\b", re.IGNORECASE),
    "central": re.compile(r"european|australian|african|\bcet\b|\bacst\b|\bacdt\b|\bcat\b", re.IGNORECASE),
    "mountain": re.compile(r"australian|\bacst\b", re.IGNORECASE),
    "pacific": re.compile(r"australian|\bapt\b", re.IGNORECASE),
    "atlantic": re.compile(r"brazil", re.IGNORECASE),
    "newfoundland": re.compile(r"$^"),
}


def _timezone(control: FormControl, value: Any) -> Any | None:
    if not control.options:
        return None
    val_str = str(value or "").strip().lower()

    # Identify candidate's time zone family
    zone_family = None
    for family, pat in _TZ_PATTERNS.items():
        if pat.search(val_str):
            zone_family = family
            break
    if not zone_family:
        return None

    family_pat = _TZ_PATTERNS[zone_family]
    exclusion_pat = _TZ_EXCLUSIONS.get(zone_family)
    candidates = [
        opt for opt in control.options
        if family_pat.search(opt) and (exclusion_pat is None or not exclusion_pat.search(opt))
    ]
    if len(candidates) == 1:
        return _wrap(control, candidates[0])
    if len(candidates) > 1:
        # Prefer options with matching GMT offset, city, or exact abbreviation
        for sub in ("gmt-5", "gmt -5", "utc-5", "utc -5", "(est)", "toronto", "new_york", "eastern standard"):
            narrowed = [c for c in candidates if sub in c.lower()]
            if len(narrowed) == 1:
                return _wrap(control, narrowed[0])
        return _wrap(control, candidates[0])
    return None


# ---------------------------------------------------------------------------
# Work Location & Arrangement Normalization
# ---------------------------------------------------------------------------

def _work_location(control: FormControl, value: Any) -> Any | None:
    if not control.options:
        return None
    val_str = str(value or "").strip().lower()
    is_remote = "remote" in val_str
    wants_canada = "canada" in val_str or "ca" in val_str

    if is_remote:
        # 1. Look for options with 'remote' and 'canada' (e.g. 'Remote within Canada', 'Remote Canada')
        if wants_canada:
            can_opts = [
                o for o in control.options
                if "remote" in o.lower() and ("canada" in o.lower() or "within canada" in o.lower())
            ]
            if len(can_opts) == 1:
                return _wrap(control, can_opts[0])
        # 2. General remote option (excluding hybrid/neither)
        rem_opts = [
            o for o in control.options
            if "remote" in o.lower() and "hybrid" not in o.lower() and "neither" not in o.lower()
        ]
        if len(rem_opts) == 1:
            return _wrap(control, rem_opts[0])

    if "hybrid" in val_str:
        hyb_opts = [o for o in control.options if "hybrid" in o.lower()]
        if len(hyb_opts) == 1:
            return _wrap(control, hyb_opts[0])

    return None


# ---------------------------------------------------------------------------
# Legal / Consent / Disclaimer Normalization
# ---------------------------------------------------------------------------

def _consent_and_disclaimer(control: FormControl, value: Any) -> Any | None:
    if not control.options:
        return None
    val_str = str(value or "").strip().lower()
    is_affirmative = val_str in _YES or any(t in val_str for t in ("agree", "consent", "acknowledge", "accept"))

    # If the form only provides a single affirmative option, e.g. ['Yes'] or ['I agree']
    if len(control.options) == 1:
        single_opt = control.options[0].strip().lower()
        if single_opt in _YES or _AGREE_PATTERNS.search(single_opt):
            return _wrap(control, control.options[0])

    if is_affirmative:
        # Look for explicit Yes
        opt_yes = _single_prefixed(control, "yes")
        if opt_yes is not None:
            return _wrap(control, opt_yes)
        # Look for agree/consent/acknowledge options
        agree_opts = [o for o in control.options if _AGREE_PATTERNS.search(o)]
        if len(agree_opts) == 1:
            return _wrap(control, agree_opts[0])

    return None


# ---------------------------------------------------------------------------
# Main Option Derivation Dispatcher
# ---------------------------------------------------------------------------

def derive_option(control: Any, value: Any, key: Any = "") -> Any | None:
    """Listed option(s) implied by ``value`` under the rules above, or None."""
    if isinstance(control, str):
        from app.form_engine import ControlType, FormControl

        key_name = control
        raw_opts = key if isinstance(key, (list, tuple, set)) else []
        control = FormControl(
            key=key_name,
            label="",
            control_type=ControlType.SELECT,
            options=list(raw_opts),
            vault_keys=[key_name],
            legal=any(t in key_name.lower() for t in ("consent", "terms", "disclaimer", "agreement")),
        )
        key = key_name

    if isinstance(key, (list, tuple, set)):
        key_str = ""
    else:
        key_str = str(key or "")

    lookup_keys = {
        key_str.lower(),
        str(getattr(control, "key", "") or "").lower(),
        *(str(k).lower() for k in getattr(control, "vault_keys", []) if k),
    }
    lookup_keys.discard("")
    label_lower = str(getattr(control, "label", "") or "").lower()

    # 1. Bilingual language proficiency
    if "language_proficiency" in lookup_keys or "language" in lookup_keys:
        bilingual_res = _bilingual(control, value)
        if bilingual_res is not None:
            return bilingual_res

    # 2. Compensation & Salary range
    if (
        any("salary" in k or "compensation" in k or "pay" in k for k in lookup_keys)
        or "salary" in label_lower
        or "compensation" in label_lower
    ):
        sal_res = _salary_range(control, value)
        if sal_res is not None:
            return sal_res

    # 3. Time zone
    if (
        "time_zone" in lookup_keys
        or "timezone" in lookup_keys
        or "time zone" in label_lower
        or "timezone" in label_lower
    ):
        tz_res = _timezone(control, value)
        if tz_res is not None:
            return tz_res

    # 4. Work location & Arrangement
    if (
        any("location" in k or "arrangement" in k or "remote" in k or "commute" in k for k in lookup_keys)
        or "work location" in label_lower
        or "remote" in label_lower
        or "hybrid" in label_lower
    ):
        loc_res = _work_location(control, value)
        if loc_res is not None:
            return loc_res

    # 5. Consent & Legal disclaimer
    if (
        getattr(control, "legal", False)
        or any("consent" in k or "terms" in k or "disclaimer" in k or "agreement" in k for k in lookup_keys)
        or _AGREE_PATTERNS.search(label_lower)
        or "disclaimer" in label_lower
    ):
        consent_res = _consent_and_disclaimer(control, value)
        if consent_res is not None:
            return consent_res

    # 6. Fallback to general Yes/No matching
    yn_res = _yes_no(control, value)
    if yn_res is not None:
        return yn_res

    ctrl_options = getattr(control, "options", []) or []
    # 7. Final fallback: try category matchers based on options structure
    if any(re.search(r"\b\d+k\b|\b\d+-\d+k\b|\$\d+", opt, re.I) for opt in ctrl_options):
        sal_res = _salary_range(control, value)
        if sal_res is not None:
            return sal_res

    if any(re.search(r"gmt[-+]|utc[-+]|eastern|pacific|central|mountain", opt, re.I) for opt in ctrl_options):
        tz_res = _timezone(control, value)
        if tz_res is not None:
            return tz_res

    if any("remote" in opt.lower() for opt in ctrl_options):
        loc_res = _work_location(control, value)
        if loc_res is not None:
            return loc_res

    return None
