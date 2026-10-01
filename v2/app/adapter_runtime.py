from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.adapters import AdapterRegistry, FieldSpec, PlatformAdapter, SubmissionResult, ValidationResult
from app.answer_vault import AnswerVault
from app.form_engine import FillPlan, detect_handoff, parse_controls, plan_fill
from app.models import AdapterMaturity


@dataclass(slots=True)
class AdapterInfo:
    name: str
    maturity: AdapterMaturity
    version: str = "2.0.0"
    feature_flag: str = ""
    notes: str = ""


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    hay = f"{host}{path}"
    if "greenhouse.io" in host or "greenhouse" in hay or "gh_jid=" in urlparse(url).query.lower():
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if host == "ashbyhq.com" or host.endswith(".ashbyhq.com"):
        return "ashby"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "myworkdayjobs.com" in host or "workday" in host:
        return "workday"
    if "icims.com" in host:
        return "icims"
    if "taleo.net" in host:
        return "taleo"
    if "mailto:" in url or url.startswith("mailto:"):
        return "email"
    return "generic"


def platform_for_job(job: Any) -> str:
    """Platform for a stored job; discovery source beats URL heuristics."""
    if getattr(job, "platform", None):
        return str(job.platform)
    source = (getattr(job, "source", "") or "").lower()
    if source in {"greenhouse", "lever", "ashby"}:
        return source
    return detect_platform(getattr(job, "url", "") or "")


class SnapshotAdapter(PlatformAdapter):
    name = "generic"
    maturity = AdapterMaturity.dry_run
    version = "2.0.0"

    def __init__(self, html: str = "", url: str = "") -> None:
        self.html = html
        self.url = url
        self.filled: dict[str, Any] = {}
        self.uploaded: dict[str, str] = {}
        self.opened = False

    async def open_application(self, url: str) -> None:
        self.url = url or self.url
        self.opened = True

    async def identify_fields(self) -> list[FieldSpec]:
        return [
            FieldSpec(
                key=control.key,
                label=control.label,
                input_type=control.control_type.value,
                required=control.required,
                options=control.options,
            )
            for control in parse_controls(self.html)
        ]

    async def fill_fields(self, answers: dict[str, Any]) -> None:
        self.filled.update(answers)

    async def upload_documents(self, resume_path: str, cover_letter_path: str | None) -> None:
        self.uploaded["resume"] = resume_path
        if cover_letter_path:
            self.uploaded["cover_letter"] = cover_letter_path

    async def validate(self) -> ValidationResult:
        missing = [key for key, value in self.filled.items() if value in (None, "")]
        return ValidationResult(ok=not missing, errors=[f"missing:{key}" for key in missing])

    async def submit(self, mode: str) -> SubmissionResult:
        if mode != "autonomous":
            digest = hashlib.sha256(f"{self.url}:{sorted(self.filled)}".encode()).hexdigest()[:12]
            return SubmissionResult(
                submitted=False,
                confirmation_url=self.url,
                confirmation_text=f"dry-run complete ({digest})",
                metadata={"mode": mode, "filled": list(self.filled), "maturity": self.maturity.value},
            )
        return SubmissionResult(
            submitted=False,
            confirmation_text="live submission blocked: adapter is not certified_autonomous",
            metadata={"mode": mode, "blocked": True},
        )


class GreenhouseAdapter(SnapshotAdapter):
    name = "greenhouse"
    maturity = AdapterMaturity.human_reviewed_submit


class LeverAdapter(SnapshotAdapter):
    name = "lever"
    maturity = AdapterMaturity.dry_run


class AshbyAdapter(SnapshotAdapter):
    name = "ashby"
    maturity = AdapterMaturity.dry_run


class EmailAdapter(SnapshotAdapter):
    name = "email"
    maturity = AdapterMaturity.dry_run


class DetectOnlyAdapter(SnapshotAdapter):
    maturity = AdapterMaturity.detect_only

    async def submit(self, mode: str) -> SubmissionResult:
        return SubmissionResult(
            submitted=False,
            confirmation_text=f"{self.name} adapter is detect_only",
            metadata={"blocked": True, "maturity": self.maturity.value},
        )


class SmartRecruitersAdapter(DetectOnlyAdapter):
    name = "smartrecruiters"


class WorkdayAdapter(DetectOnlyAdapter):
    name = "workday"


class IcimsAdapter(DetectOnlyAdapter):
    name = "icims"


class TaleoAdapter(DetectOnlyAdapter):
    name = "taleo"


REGISTRY = AdapterRegistry()
for adapter in (
    GreenhouseAdapter,
    LeverAdapter,
    AshbyAdapter,
    EmailAdapter,
    SnapshotAdapter,
    SmartRecruitersAdapter,
    WorkdayAdapter,
    IcimsAdapter,
    TaleoAdapter,
):
    REGISTRY.register(adapter)


ADAPTER_CATALOG: dict[str, AdapterInfo] = {
    "greenhouse": AdapterInfo("greenhouse", AdapterMaturity.human_reviewed_submit, feature_flag="adapter.greenhouse", notes="Dry-run certified locally; live submit stays gated."),
    "lever": AdapterInfo("lever", AdapterMaturity.dry_run, feature_flag="adapter.lever", notes="Real-form dry-run from the public /apply page."),
    "ashby": AdapterInfo("ashby", AdapterMaturity.dry_run, feature_flag="adapter.ashby", notes="Real-form dry-run from Ashby's public form API (read via GET)."),
    "email": AdapterInfo("email", AdapterMaturity.dry_run, feature_flag="adapter.email"),
    "generic": AdapterInfo("generic", AdapterMaturity.dry_run, feature_flag="adapter.generic"),
    "smartrecruiters": AdapterInfo("smartrecruiters", AdapterMaturity.detect_only, feature_flag="adapter.smartrecruiters"),
    "workday": AdapterInfo("workday", AdapterMaturity.detect_only, feature_flag="adapter.workday"),
    "icims": AdapterInfo("icims", AdapterMaturity.detect_only, feature_flag="adapter.icims"),
    "taleo": AdapterInfo("taleo", AdapterMaturity.detect_only, feature_flag="adapter.taleo"),
}


def plan_for_html(html: str, vault: AnswerVault) -> tuple[str | None, FillPlan]:
    handoff = detect_handoff(html)
    plan = plan_fill(parse_controls(html), vault)
    return handoff, plan


def greenhouse_board_url(token: str, job_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]", "", token)
    job_id = re.sub(r"[^0-9A-Za-z_-]", "", job_id)
    return f"https://boards.greenhouse.io/{token}/jobs/{job_id}"
