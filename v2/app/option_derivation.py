"""Deterministic option derivation when a resolved answer is not literally an option.

Only narrow, explainable rules; anything else stays in review:

- yes/no answers pick the single option that starts with "Yes" / "No";
- verified ``language_proficiency`` answers bilingual English+French questions:
  a Yes/No question gets "Yes" only when both languages are at professional
  working level or above, and a CEFR-scale question gets the weaker of the two
  languages' levels;
- a single-language fluency question ("Are you fluent in French?") gets a plain
  "Yes" only when that language is at professional working level or above,
  otherwise review; never the whole stored language list;
- date-picker fields only take a concrete calendar date, written as ISO
  ``YYYY-MM-DD``; relative answers ("2 weeks notice") go to review.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
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


def _single_prefixed(control: FormControl, word: str) -> str | None:
    pattern = re.compile(rf"^\s*{word}\b", re.IGNORECASE)
    candidates = [option for option in control.options if pattern.search(option)]
    return candidates[0] if len(candidates) == 1 else None


def _wrap(control: FormControl, option: str | None) -> Any | None:
    if option is None:
        return None
    return [option] if control.control_type.value == "multiselect" else option


def _yes_no(control: FormControl, value: Any) -> Any | None:
    text = " ".join(str(value).split()).casefold()
    if text in _YES:
        return _wrap(control, _single_prefixed(control, "yes"))
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


def derive_option(control: FormControl, value: Any, key: str = "") -> Any | None:
    """Listed option(s) implied by ``value`` under the rules above, or None."""
    if key == "language_proficiency" or "language_proficiency" in control.vault_keys:
        return _bilingual(control, value)
    return _yes_no(control, value)


_KNOWN_LANGUAGES = ("english", "french", "spanish", "german", "portuguese", "italian", "mandarin", "arabic")
_SINGLE_LANGUAGE_RE = re.compile(
    r"^\s*(?:are you|do you|can you|is your)\b.{0,40}\b(?:fluent|speak|proficient|bilingual|work)\b",
    re.IGNORECASE,
)


def single_language_answer(control: FormControl, value: Any, key: str = "") -> str | list[str] | None:
    """Yes for a one-language yes/no fluency question.

    Returns None when the rule does not apply, "" when it applies but the
    stored level is below professional working (review), else the answer.
    """
    if key != "language_proficiency":
        return None
    label = " ".join((control.label or "").split()).casefold()
    named = [lang for lang in _KNOWN_LANGUAGES if re.search(rf"\b{lang}\b", label)]
    if len(named) != 1 or not _SINGLE_LANGUAGE_RE.search(label):
        return None
    level = parse_languages(value).get(named[0])
    if level is None or _CEFR_ORDER.index(level) < _CEFR_ORDER.index(_PROFESSIONAL):
        return ""
    if not control.options:
        return "Yes"
    option = _wrap(control, _single_prefixed(control, "yes"))
    return option if option is not None else ""


_DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")


def iso_date(value: Any) -> str | None:
    """``YYYY-MM-DD`` for a concrete, unambiguous date; else None."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = " ".join(str(value or "").split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).date().isoformat()
        except ValueError:
            continue
    return None
