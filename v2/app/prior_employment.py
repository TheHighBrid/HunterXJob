"""Answer "have you worked for <employer> before?" from verified employment history.

Fail-closed rule agreed for HunterXJob:

- Answer **No** only when the posting's employer clearly does not appear in the
  verified employment history.
- A match, a near-match (lookalike name, shared word, high string similarity)
  or an empty history goes to review. Never a global "No".

The employer name comes from the ATS board slug and must also appear in the
question label, so a question about some other entity (a government, a
partner) is never answered by this rule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from app.answer_vault import FieldRequest, ResolutionStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.answer_vault import AnswerVault
    from app.form_engine import FormControl

PRIOR_EMPLOYMENT_RE = re.compile(
    r"previous(?:ly)? (?:worked|employed|been employed)|worked (?:at|for) .* (?:before|previously|in the past)|"
    r"(?:employed|engaged)[^?]* in the past|consulted for"
)
_NO_OPTION_RE = re.compile(r"^\s*no\b", re.IGNORECASE)
_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "gmbh", "plc", "sa", "ag", "bv", "the", "group", "holdings",
})
_MAX_HISTORY = 50
SIMILARITY_THRESHOLD = 0.8


@dataclass(frozen=True, slots=True)
class PriorEmploymentDecision:
    status: str  # "fill" or "review"
    value: Any = None
    reason: str = ""


def _words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", name.casefold()) if w not in _SUFFIXES]


def employer_in_label(board: str, label: str) -> str:
    """Board slug as an employer name when the label names it; else ""."""
    words = _words(board.replace("-", " ").replace("_", " "))
    if not words:
        return ""
    label_words = _words(label)
    joined = "".join(label_words)
    name = " ".join(words)
    if all(w in label_words for w in words) or "".join(words) in joined:
        return name
    return ""


def tag_prior_employer(controls: list[FormControl], board: str) -> None:
    """Mark prior-employment questions with the posting's employer name."""
    for control in controls:
        label = " ".join((control.label or "").split()).lower()
        if PRIOR_EMPLOYMENT_RE.search(label):
            control.employer = employer_in_label(board, label)


def verified_employers(vault: AnswerVault) -> list[str]:
    names: list[str] = []
    for index in range(_MAX_HISTORY):
        key = f"employment_{index}_company"
        if not vault.has(key):
            break
        resolved = vault.resolve(FieldRequest(key=key, required=False, sensitive=False))
        if resolved.status is ResolutionStatus.RESOLVED and str(resolved.value or "").strip():
            names.append(str(resolved.value))
    return names


def looks_like(employer: str, known: str) -> bool:
    """True for an exact match or any near-match that a human should check."""
    target, other = _words(employer), _words(known)
    if not target or not other:
        return False
    if set(target) & set(other):
        return True
    a, b = "".join(target), "".join(other)
    if a in b or b in a:
        return True
    pairs = [(a, b)] + [(t, o) for t in target for o in other]
    return any(SequenceMatcher(None, x, y).ratio() >= SIMILARITY_THRESHOLD for x, y in pairs)


def _no_option(control: FormControl) -> Any | None:
    candidates = [option for option in control.options if _NO_OPTION_RE.search(option)]
    if len(candidates) != 1:
        return None
    return [candidates[0]] if control.control_type.value == "multiselect" else candidates[0]


def decide(control: FormControl, vault: AnswerVault) -> PriorEmploymentDecision | None:
    """Decision for a tagged prior-employment control, or None if not one."""
    employer = getattr(control, "employer", "")
    if not employer:
        return None
    history = verified_employers(vault)
    if not history:
        return PriorEmploymentDecision("review", reason="prior employment: no verified employment history")
    matches = [name for name in history if looks_like(employer, name)]
    if matches:
        return PriorEmploymentDecision("review", reason=f"prior employment: possible match with verified employer {matches[0]!r}")
    option = _no_option(control)
    if control.options and option is None:
        return PriorEmploymentDecision("review", reason="prior employment: no single 'No' option to select")
    return PriorEmploymentDecision(
        "fill", value=option if control.options else "No",
        reason=f"prior employment: {employer!r} not in {len(history)} verified employers",
    )
