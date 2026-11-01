"""Deterministic option derivation when a resolved answer is not literally an option.

Only narrow, explainable rules; anything else stays in review:

- yes/no answers pick the single option that starts with "Yes" / "No";
- verified ``language_proficiency`` answers bilingual English+French questions:
  a Yes/No question gets "Yes" only when both languages are at professional
  working level or above, and a CEFR-scale question gets the weaker of the two
  languages' levels.
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
