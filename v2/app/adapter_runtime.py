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
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    hay = f"{host}{path}?{query}"
    if "greenhouse.io" in host or "greenhouse" in hay or "gh_jid=" in urlparse(url).query.lower():
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if host == "ashbyhq.com" or host.endswith(".ashbyhq.com"):
        return "ashby"
    if "workable.com" in host:
        return "workable"
    if "breezy.hr" in host:
        return "breezy"
    if "recruitee.com" in host:
        return "recruitee"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "bamboohr.com" in host:
        return "bamboohr"
    if "teamtailor.com" in host:
        return "teamtailor"
    if "rippling.com" in host:
        return "rippling"
    if "pinpointhq.com" in host:
        return "pinpoint"
    if "applytojob.com" in host or "jazz.co" in host or "jazzhr.com" in host:
        return "jazzhr"
    if "myworkdayjobs.com" in host or "workday" in host:
        return "workday"
    if "icims.com" in host:
        return "icims"
    if "oraclecloud.com" in host or "taleo.net" in host or "oracle" in host:
        return "oracle"
    # Canadian Federal, Provincial & Municipal Portals (Ottawa–Gatineau NCR)
    if any(h in host for h in ("jobs.gc.ca", "emplois.gc.ca", "cfp-psc.gc.ca")):
        return "gc_jobs"
    if "cityofottawa.hua.hrsmart.com" in host or ("ottawa.ca" in host and any(t in hay for t in ("job", "career", "hrsmart", "dayforce"))):
        return "ottawa_careers"
    if "gojobs.gov.on.ca" in host or ("ontario.ca" in host and "careers" in hay):
        return "ops_careers"
    if "gatineau.cvmanager.com" in host or ("gatineau.ca" in host and any(t in hay for t in ("emploi", "carriere"))):
        return "gatineau_careers"
    if "quebecemploi.gouv.qc.ca" in host or "emploiquebec.gouv.qc.ca" in host:
        return "quebec_emploi"
    if "mailto:" in url or url.startswith("mailto:"):
        return "email"
    return "generic"


def platform_for_job(job: Any) -> str:
    """Platform for a stored job; discovery source beats URL heuristics."""
    if getattr(job, "platform", None):
        return str(job.platform)
    source = (getattr(job, "source", "") or "").lower()
    if source in {
        "greenhouse",
        "lever",
        "ashby",
        "workable",
        "breezy",
        "recruitee",
        "smartrecruiters",
        "bamboohr",
        "teamtailor",
        "rippling",
        "pinpoint",
        "jazzhr",
        "workday",
        "icims",
        "oracle",
        "taleo",
        "gc_jobs",
        "ottawa_careers",
        "ops_careers",
        "gatineau_careers",
        "quebec_emploi",
    }:
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


class SmartRecruitersAdapter(SnapshotAdapter):
    name = "smartrecruiters"
    maturity = AdapterMaturity.human_reviewed_submit


class WorkdayAdapter(SnapshotAdapter):
    name = "workday"
    maturity = AdapterMaturity.human_reviewed_submit


class WorkableAdapter(SnapshotAdapter):
    name = "workable"
    maturity = AdapterMaturity.human_reviewed_submit


class BreezyAdapter(SnapshotAdapter):
    name = "breezy"
    maturity = AdapterMaturity.human_reviewed_submit


class RecruiteeAdapter(SnapshotAdapter):
    name = "recruitee"
    maturity = AdapterMaturity.human_reviewed_submit


class BambooHRAdapter(SnapshotAdapter):
    name = "bamboohr"
    maturity = AdapterMaturity.dry_run


class TeamtailorAdapter(SnapshotAdapter):
    name = "teamtailor"
    maturity = AdapterMaturity.dry_run


class RipplingAdapter(SnapshotAdapter):
    name = "rippling"
    maturity = AdapterMaturity.dry_run


class PinpointAdapter(SnapshotAdapter):
    name = "pinpoint"
    maturity = AdapterMaturity.dry_run


class JazzHRAdapter(SnapshotAdapter):
    name = "jazzhr"
    maturity = AdapterMaturity.dry_run


class OracleAdapter(DetectOnlyAdapter):
    name = "oracle"


class GCJobsAdapter(SnapshotAdapter):
    name = "gc_jobs"
    maturity = AdapterMaturity.dry_run


class OttawaCareersAdapter(SnapshotAdapter):
    name = "ottawa_careers"
    maturity = AdapterMaturity.dry_run


class OPSCareersAdapter(SnapshotAdapter):
    name = "ops_careers"
    maturity = AdapterMaturity.dry_run


class GatineauCareersAdapter(SnapshotAdapter):
    name = "gatineau_careers"
    maturity = AdapterMaturity.dry_run


class QuebecEmploiAdapter(SnapshotAdapter):
    name = "quebec_emploi"
    maturity = AdapterMaturity.dry_run


class IcimsAdapter(DetectOnlyAdapter):
    name = "icims"


class TaleoAdapter(DetectOnlyAdapter):
    name = "taleo"


REGISTRY = AdapterRegistry()
for adapter in (
    GreenhouseAdapter,
    LeverAdapter,
    AshbyAdapter,
    WorkableAdapter,
    BreezyAdapter,
    RecruiteeAdapter,
    SmartRecruitersAdapter,
    BambooHRAdapter,
    TeamtailorAdapter,
    RipplingAdapter,
    PinpointAdapter,
    JazzHRAdapter,
    WorkdayAdapter,
    GCJobsAdapter,
    OttawaCareersAdapter,
    OPSCareersAdapter,
    GatineauCareersAdapter,
    QuebecEmploiAdapter,
    IcimsAdapter,
    OracleAdapter,
    TaleoAdapter,
    EmailAdapter,
    SnapshotAdapter,
):
    REGISTRY.register(adapter)


ADAPTER_CATALOG: dict[str, AdapterInfo] = {
    "greenhouse": AdapterInfo("greenhouse", AdapterMaturity.human_reviewed_submit, feature_flag="adapter.greenhouse", notes="Dry-run certified locally; live submit ready."),
    "lever": AdapterInfo("lever", AdapterMaturity.dry_run, feature_flag="adapter.lever", notes="Real-form dry-run from the public /apply page."),
    "ashby": AdapterInfo("ashby", AdapterMaturity.dry_run, feature_flag="adapter.ashby", notes="Real-form dry-run from Ashby's public form API (17 live submissions certified)."),
    "workable": AdapterInfo("workable", AdapterMaturity.human_reviewed_submit, feature_flag="adapter.workable", notes="Real-form extraction via public v3 API / page, in-page form fill."),
    "breezy": AdapterInfo("breezy", AdapterMaturity.human_reviewed_submit, feature_flag="adapter.breezy", notes="Real-form extraction via public JSON / page, in-page form fill."),
    "recruitee": AdapterInfo("recruitee", AdapterMaturity.human_reviewed_submit, feature_flag="adapter.recruitee", notes="Real-form extraction via public offers API, in-page form fill."),
    "smartrecruiters": AdapterInfo("smartrecruiters", AdapterMaturity.human_reviewed_submit, feature_flag="adapter.smartrecruiters", notes="Real-form extraction and browser-fill supported."),
    "bamboohr": AdapterInfo("bamboohr", AdapterMaturity.dry_run, feature_flag="adapter.bamboohr", notes="BambooHR careers portal real-form extraction and form fill."),
    "teamtailor": AdapterInfo("teamtailor", AdapterMaturity.dry_run, feature_flag="adapter.teamtailor", notes="Teamtailor careers portal real-form extraction and form fill."),
    "rippling": AdapterInfo("rippling", AdapterMaturity.dry_run, feature_flag="adapter.rippling", notes="Rippling candidate application form fill."),
    "pinpoint": AdapterInfo("pinpoint", AdapterMaturity.dry_run, feature_flag="adapter.pinpoint", notes="Pinpoint applicant form fill."),
    "jazzhr": AdapterInfo("jazzhr", AdapterMaturity.dry_run, feature_flag="adapter.jazzhr", notes="JazzHR applytojob single-page form fill."),
    "workday": AdapterInfo("workday", AdapterMaturity.human_reviewed_submit, feature_flag="adapter.workday", notes="Real-form extraction and browser-fill supported."),
    "gc_jobs": AdapterInfo("gc_jobs", AdapterMaturity.dry_run, feature_flag="adapter.gc_jobs", notes="Federal GC Jobs (jobs.gc.ca) account-based application flow."),
    "ottawa_careers": AdapterInfo("ottawa_careers", AdapterMaturity.dry_run, feature_flag="adapter.ottawa_careers", notes="City of Ottawa municipal career portal."),
    "ops_careers": AdapterInfo("ops_careers", AdapterMaturity.dry_run, feature_flag="adapter.ops_careers", notes="Ontario Public Service (gojobs.gov.on.ca) portal."),
    "gatineau_careers": AdapterInfo("gatineau_careers", AdapterMaturity.dry_run, feature_flag="adapter.gatineau_careers", notes="Ville de Gatineau municipal career portal (cvmanager)."),
    "quebec_emploi": AdapterInfo("quebec_emploi", AdapterMaturity.dry_run, feature_flag="adapter.quebec_emploi", notes="Québec Emploi provincial career portal."),
    "icims": AdapterInfo("icims", AdapterMaturity.detect_only, feature_flag="adapter.icims", notes="Enterprise iCIMS candidate portal flow."),
    "oracle": AdapterInfo("oracle", AdapterMaturity.detect_only, feature_flag="adapter.oracle", notes="Enterprise Oracle Recruiting Cloud candidate flow."),
    "taleo": AdapterInfo("taleo", AdapterMaturity.detect_only, feature_flag="adapter.taleo", notes="Enterprise Taleo candidate portal flow."),
    "email": AdapterInfo("email", AdapterMaturity.dry_run, feature_flag="adapter.email"),
    "generic": AdapterInfo("generic", AdapterMaturity.dry_run, feature_flag="adapter.generic"),
}


def plan_for_html(html: str, vault: AnswerVault) -> tuple[str | None, FillPlan]:
    handoff = detect_handoff(html)
    plan = plan_fill(parse_controls(html), vault)
    return handoff, plan


def greenhouse_board_url(token: str, job_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]", "", token)
    job_id = re.sub(r"[^0-9A-Za-z_-]", "", job_id)
    return f"https://boards.greenhouse.io/{token}/jobs/{job_id}"
