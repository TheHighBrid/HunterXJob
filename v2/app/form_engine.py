from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from typing import Any

from app import prior_employment
from app.answer_vault import AnswerVault, FieldRequest, ResolutionStatus
from app.option_derivation import derive_option


class ControlType(str, Enum):
    TEXT = "text"
    EMAIL = "email"
    TEL = "tel"
    NUMBER = "number"
    # HTML <input type="password"> control type, not a credential.
    PASSWORD = "password"  # noqa  # nosemgrep  # nosec B105
    TEXTAREA = "textarea"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    MULTISELECT = "multiselect"
    DATE = "date"
    AUTOCOMPLETE = "autocomplete"
    FILE = "file"
    HIDDEN = "hidden"
    UNKNOWN = "unknown"


SENSITIVE_PATTERNS = (
    "gender", "race", "ethnicity", "disability", "veteran", "hispanic",
    "sexual orientation", "pronoun",
)
LEGAL_PATTERNS = (
    "i agree", "terms", "privacy", "consent", "certify", "acknowledge",
    "accurate", "true and complete", "disclaimer", "export control",
    "criminal", "convicted", "background check", "non-compete",
    "post-employment restriction", "employment agreement",
)
AUTH_PATTERNS = (
    "work authorization", "authorized to work", "sponsorship", "visa",
    "citizen", "permanent resident", "work permit", "eligible to work",
    "right to work", "security clearance",
)
# Sections whose answers are voluntary self-identification. They are never
# inferred; they are filled only from explicit answers or an explicit
# decline-to-self-identify policy stored in the answer vault.
VOLUNTARY_SECTIONS = frozenset({"eeoc", "demographic"})
DECLINE_POLICY_KEY = "voluntary_self_identification"
_DECLINE_OPTION_RE = re.compile(
    r"decline|don.?t wish|do not wish|prefer not|not to (?:say|disclose|answer|respond|self)|choose not|wish not",
    re.IGNORECASE,
)
ASSESSMENT_PATTERNS = ("assessment", "hirevue", "codility", "hackerrank", "personality test")
CAPTCHA_PATTERNS = ("captcha", "recaptcha", "hcaptcha", "verify you are human", "cf-challenge")


@dataclass(slots=True)
class FormControl:
    key: str
    label: str
    control_type: ControlType
    required: bool = False
    options: list[str] = field(default_factory=list)
    value: Any = None
    name: str = ""
    input_type: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.5
    sensitive: bool = False
    legal: bool = False
    # Where the control came from on the real form (e.g. "application",
    # "location", "eeoc", "demographic", "data_compliance", "dom").
    section: str = "application"
    # Additional vault keys to try, most specific first, before ``key``.
    vault_keys: list[str] = field(default_factory=list)
    # Option label -> platform value id, when the platform exposes one.
    option_values: dict[str, str] = field(default_factory=dict)
    # Posting employer for "worked here before?" questions (see prior_employment).
    employer: str = ""


@dataclass(slots=True)
class FillPlanItem:
    control: FormControl
    status: str
    value: Any = None
    reason: str = ""


@dataclass(slots=True)
class FillPlan:
    items: list[FillPlanItem]
    blockers: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blockers and all(item.status == "fill" for item in self.items if item.control.required)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.controls: list[dict[str, Any]] = []
        self._label_for: dict[str, str] = {}
        self._open_label: list[str] = []
        self._open_select: dict[str, Any] | None = None
        self._current_option_value = ""
        self.page_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k: (v or "") for k, v in attrs}
        if tag == "label":
            self._open_label.append(data.get("for", ""))
        elif tag == "input":
            self.controls.append({"tag": "input", **data, "label": ""})
        elif tag == "textarea":
            self.controls.append({"tag": "textarea", **data, "label": ""})
        elif tag == "select":
            self._open_select = {"tag": "select", **data, "label": "", "options": []}
        elif tag == "option" and self._open_select is not None:
            self._current_option_value = data.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._open_label:
            self._open_label.pop()
        elif tag == "select" and self._open_select is not None:
            self.controls.append(self._open_select)
            self._open_select = None

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        self.page_text_parts.append(text)
        if self._open_label:
            target = self._open_label[-1]
            if target:
                self._label_for[target] = (self._label_for.get(target, "") + " " + text).strip()
            elif self.controls:
                self.controls[-1]["label"] = (self.controls[-1].get("label", "") + " " + text).strip()
        if self._open_select is not None and self._current_option_value is not None:
            option_label = text
            value = self._current_option_value or option_label
            self._open_select["options"].append(value)
            self._current_option_value = ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "field"


def _classify_type(raw: dict[str, Any]) -> ControlType:
    tag = raw.get("tag", "")
    itype = (raw.get("type") or "text").lower()
    role = (raw.get("role") or "").lower()
    multiple = raw.get("multiple") is not None and raw.get("multiple") != "false"
    if tag == "select":
        return ControlType.MULTISELECT if multiple else ControlType.SELECT
    if tag == "textarea":
        return ControlType.TEXTAREA
    mapping = {
        "email": ControlType.EMAIL,
        "tel": ControlType.TEL,
        "number": ControlType.NUMBER,
        "password": ControlType.PASSWORD,
        "radio": ControlType.RADIO,
        "checkbox": ControlType.CHECKBOX,
        "date": ControlType.DATE,
        "file": ControlType.FILE,
        "hidden": ControlType.HIDDEN,
        "text": ControlType.TEXT,
    }
    if role in {"combobox", "autocomplete"}:
        return ControlType.AUTOCOMPLETE
    return mapping.get(itype, ControlType.UNKNOWN if itype not in {"text", "search"} else ControlType.TEXT)


def _looks_like(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def parse_controls(html: str) -> list[FormControl]:
    parser = _FormParser()
    parser.feed(html or "")
    controls: list[FormControl] = []
    for raw in parser.controls:
        ident = raw.get("id") or raw.get("name") or raw.get("aria-label") or raw.get("placeholder") or "field"
        label = raw.get("label") or parser._label_for.get(raw.get("id", ""), "") or raw.get("aria-label") or raw.get("placeholder") or ident
        control_type = _classify_type(raw)
        blob = " ".join([label, raw.get("name", ""), raw.get("id", ""), raw.get("autocomplete", "")])
        required = "required" in raw and str(raw.get("required")).lower() not in {"false", "0"}
        confidence = 0.9 if label and raw.get("name") else 0.55
        if control_type is ControlType.UNKNOWN:
            confidence = 0.2
        controls.append(FormControl(
            key=_slug(raw.get("name") or ident),
            label=str(label).strip(),
            control_type=control_type,
            required=required,
            options=list(raw.get("options") or []),
            name=raw.get("name", ""),
            input_type=raw.get("type", ""),
            evidence=[f"tag={raw.get('tag')}", f"name={raw.get('name', '')}", f"id={raw.get('id', '')}"],
            confidence=confidence,
            sensitive=_looks_like(blob, SENSITIVE_PATTERNS) or _looks_like(blob, AUTH_PATTERNS),
            legal=_looks_like(blob, LEGAL_PATTERNS),
        ))
    return controls


def detect_handoff(html: str, url: str = "") -> str | None:
    blob = f"{html} {url}".lower()
    if _looks_like(blob, CAPTCHA_PATTERNS):
        return "captcha_detected"
    if "cloudflare" in blob and "challenge" in blob:
        return "anti_bot_challenge"
    if _looks_like(blob, ASSESSMENT_PATTERNS):
        return "assessment_required"
    if re.search(r"type=['\"]password['\"]", blob) and any(token in blob for token in ("sign in", "log in", "login")):
        return "login_required"
    return None


def _normalize_option(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _score_best_option(answer: Any, options: list[str]) -> str | None:
    """Score options against answer and return unique winner if score >= 70."""
    from app.question_catalog import OptionRecord, score_option

    ans_str = str(answer or "").strip()
    if not ans_str or not options:
        return None

    scored: list[tuple[int, str]] = []
    for opt in options:
        rec = OptionRecord(key=opt, label=opt, value=opt)
        score = score_option(ans_str, rec)
        if score >= 70:
            scored.append((score, opt))

    if not scored:
        return None

    scored.sort(key=lambda s: s[0], reverse=True)
    top_score = scored[0][0]
    top_candidates = [opt for score, opt in scored if score == top_score]

    # If there is a tie between multiple distinct options, fail closed (ambiguous)
    if len(top_candidates) == 1:
        return top_candidates[0]
    return None


def _match_single_option(control: FormControl, val: str, lookup: dict[str, str]) -> str | None:
    norm = _normalize_option(val)
    if norm in lookup:
        return lookup[norm]
    derived = derive_option(control, val)
    if derived is not None:
        if isinstance(derived, list):
            return derived[0] if derived else None
        return derived
    best = _score_best_option(val, control.options)
    if best is not None:
        return best
    return None


def _match_option(control: FormControl, value: Any) -> Any | None:
    """Return the listed option(s) matching ``value`` or None."""
    lookup = {_normalize_option(option): option for option in control.options}
    if control.control_type is ControlType.MULTISELECT:
        parts = value if isinstance(value, (list, tuple)) else [part for part in str(value).split("|") if part.strip()]
        matched = [_match_single_option(control, part, lookup) for part in parts]
        if not matched or any(item is None for item in matched):
            return None
        return matched
    return _match_single_option(control, str(value), lookup)


def _decline_option(control: FormControl) -> str | None:
    candidates = [option for option in control.options if _DECLINE_OPTION_RE.search(option)]
    return candidates[0] if len(candidates) == 1 else None


def _resolve_control(control: FormControl, vault: AnswerVault, request_sensitive: bool):
    keys = [*control.vault_keys, control.key]

    # Exhaustive fallback keys derived from control properties and semantic patterns
    lbl = (control.label or "").lower()
    if control.legal or any(w in lbl for w in ("disclaimer", "consent", "agree", "certify", "acknowledge")):
        keys.extend(["terms_consent", "agreement_consent", "general_consent"])
    if "salary" in lbl or "compensation" in lbl or "pay" in lbl:
        keys.extend(["salary_expectation", "desired_salary", "compensation_expectation"])
    if "time zone" in lbl or "timezone" in lbl:
        keys.extend(["time_zone", "timezone"])
    if "work location" in lbl or ("remote" in lbl and "hybrid" in lbl):
        keys.extend(["work_location_preference", "work_arrangement_nonlocal"])
    if "gender" in lbl:
        keys.extend(["gender", "gender_identity"])
    if ("veteran" in lbl or "armed forces" in lbl) and control.options:
        keys.extend(["veteran_status", "veteran"])
    if "race" in lbl or "ethnic" in lbl:
        keys.extend(["race_ethnicity", "race"])
    if "disab" in lbl:
        keys.extend(["disability", "disability_status"])
    if "how did you hear" in lbl or "hear about" in lbl:
        keys.extend(["referral_source", "hear_about_us"])
    if "onboard" in lbl and "date" in lbl:
        keys.extend(["start_date", "availability_date"])

    seen: list[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.append(key)
        if vault.has(key):
            res = vault.resolve(FieldRequest(key=key, required=control.required, sensitive=request_sensitive))
            if res.status is ResolutionStatus.RESOLVED:
                return res
    return vault.resolve(FieldRequest(key=control.key, required=control.required, sensitive=request_sensitive))


def _try_decline_option(control: FormControl, vault: AnswerVault) -> str | None:
    """Return a unique decline option when an explicit USER/POLICY decline applies."""
    if not control.options:
        return None
    if control.section not in VOLUNTARY_SECTIONS and not (control.sensitive and not control.required):
        return None
    policy = vault.resolve(FieldRequest(key=DECLINE_POLICY_KEY, required=False, sensitive=True))
    if policy.status is ResolutionStatus.RESOLVED and _normalize_option(policy.value) == "decline":
        return _decline_option(control)
    return None


def plan_fill(controls: list[FormControl], vault: AnswerVault) -> FillPlan:
    items: list[FillPlanItem] = []
    blockers: list[str] = []
    for control in controls:
        if control.control_type is ControlType.HIDDEN:
            continue
        if control.control_type is ControlType.UNKNOWN or control.confidence < 0.4:
            if not control.required:
                items.append(FillPlanItem(control, "skip", reason="optional unsupported control skipped"))
                continue
            blockers.append("unsupported_control")
            items.append(FillPlanItem(control, "review", reason="unsupported or low-confidence control"))
            continue
        sensitive = control.sensitive or control.legal
        resolved = _resolve_control(control, vault, sensitive)
        # An explicit stored answer for this exact question always wins; the
        # history rule only covers prior-employment questions left unanswered.
        prior = None if sensitive or resolved.status is ResolutionStatus.RESOLVED else prior_employment.decide(control, vault)
        if prior is not None:
            if prior.status != "fill":
                blockers.append("ambiguous_question")
            items.append(FillPlanItem(control, prior.status, value=prior.value, reason=prior.reason))
            continue
        if resolved.status is ResolutionStatus.RESOLVED:
            value = resolved.value
            if control.options:
                matched = _match_option(control, value)
                if matched is None:
                    matched = derive_option(control, value, resolved.key)
                if matched is None:
                    decline_opt = _try_decline_option(control, vault)
                    if decline_opt is not None:
                        items.append(FillPlanItem(control, "fill", value=decline_opt, reason="explicit decline-to-self-identify policy"))
                        continue
                    if not control.required:
                        items.append(FillPlanItem(control, "skip", reason="optional question skipped (resolved value not in listed options)"))
                        continue
                    blockers.append("ambiguous_question")
                    items.append(FillPlanItem(control, "review", value=value, reason="resolved value is not a listed option"))
                    continue
                value = matched
            items.append(FillPlanItem(control, "fill", value=value, reason=resolved.reason))
            continue
        decline_opt = _try_decline_option(control, vault)
        if decline_opt is not None:
            items.append(FillPlanItem(control, "fill", value=decline_opt, reason="explicit decline-to-self-identify policy"))
            continue
        if control.legal and control.options and len(control.options) == 1:
            derived_legal = derive_option(control, "Yes", "terms_consent")
            if derived_legal is not None:
                items.append(FillPlanItem(control, "fill", value=derived_legal, reason="universal affirmative applicant consent"))
                continue
        if control.required or control.legal or (control.section == "eeoc" and control.sensitive):
            code = "sensitive_answer_missing" if control.sensitive else (
                "legal_answer_missing" if control.legal else "ambiguous_question"
            )
            blockers.append(code)
            items.append(FillPlanItem(control, "review", reason=resolved.reason))
        else:
            items.append(FillPlanItem(control, "skip", reason=f"optional question skipped ({resolved.reason})"))
    return FillPlan(items=items, blockers=sorted(set(blockers)))


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def phone_values_equivalent(actual: Any, expected: Any) -> bool:
    """Verify equivalent national, international, and formatted phone representations.

    Handles differences in dial code prefixes (+1), European national trunk zeros
    (020/01 vs +44/+33), punctuation, parentheses, and masked DOM inputs.
    """
    actual_digits = _digits(actual)
    expected_digits = _digits(expected)
    if not actual_digits or not expected_digits:
        return False
    if min(len(actual_digits), len(expected_digits)) < 7:
        return False
    if actual_digits == expected_digits:
        return True
    if actual_digits.endswith(expected_digits) or expected_digits.endswith(actual_digits):
        return True
    # European national trunk prefix 0 (e.g. 020... in UK, 01... in France)
    actual_trim = actual_digits.lstrip("0")
    expected_trim = expected_digits.lstrip("0")
    if min(len(actual_trim), len(expected_trim)) >= 7:
        if actual_digits.endswith(expected_trim) or expected_digits.endswith(actual_trim):
            return True
    return False


def verify_filled(control: FormControl, expected: Any, actual: Any) -> bool:
    if expected is None:
        return True
    if control.control_type == ControlType.CHECKBOX:
        return bool(actual) == bool(expected)
    if control.control_type is ControlType.TEL or "phone" in (control.key or "").lower():
        return phone_values_equivalent(actual, expected)
    return str(actual).strip() == str(expected).strip()
