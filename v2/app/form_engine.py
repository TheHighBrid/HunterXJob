from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from typing import Any

from app.answer_vault import AnswerVault, FieldRequest, ResolutionStatus


class ControlType(str, Enum):
    TEXT = "text"
    EMAIL = "email"
    TEL = "tel"
    NUMBER = "number"
    PASSWORD = "password"
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
    "accurate", "true and complete",
)
AUTH_PATTERNS = (
    "work authorization", "authorized to work", "sponsorship", "visa",
    "citizen", "permanent resident", "work permit",
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


def plan_fill(controls: list[FormControl], vault: AnswerVault) -> FillPlan:
    items: list[FillPlanItem] = []
    blockers: list[str] = []
    for control in controls:
        if control.control_type is ControlType.HIDDEN:
            continue
        if control.control_type is ControlType.UNKNOWN or control.confidence < 0.4:
            blockers.append("unsupported_control")
            items.append(FillPlanItem(control, "review", reason="unsupported or low-confidence control"))
            continue
        request = FieldRequest(key=control.key, required=control.required, sensitive=control.sensitive or control.legal)
        resolved = vault.resolve(request)
        if resolved.status is ResolutionStatus.RESOLVED:
            value = resolved.value
            if control.options and str(value) not in control.options:
                blockers.append("ambiguous_question")
                items.append(FillPlanItem(control, "review", value=value, reason="resolved value is not a listed option"))
                continue
            items.append(FillPlanItem(control, "fill", value=value, reason=resolved.reason))
            continue
        if control.required or control.sensitive or control.legal:
            code = "sensitive_answer_missing" if control.sensitive else (
                "legal_answer_missing" if control.legal else "ambiguous_question"
            )
            blockers.append(code)
            items.append(FillPlanItem(control, "review", reason=resolved.reason))
        else:
            items.append(FillPlanItem(control, "skip", reason=resolved.reason))
    return FillPlan(items=items, blockers=sorted(set(blockers)))


def verify_filled(control: FormControl, expected: Any, actual: Any) -> bool:
    if expected is None:
        return True
    if control.control_type in {ControlType.CHECKBOX}:
        return bool(actual) == bool(expected)
    return str(actual).strip() == str(expected).strip()
