"""Resolve the real application form for a job before a dry-run.

There is deliberately no sample-form fallback here: if the employer's real
form cannot be obtained, :class:`FormFetchError` propagates and the pipeline
routes the application to review.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import httpx

from app.adapter_runtime import platform_for_job
from app.ashby_form import ASHBY_READ_OPS, AshbyJobRef, ashby_ref_for_job, fetch_ashby_form
from app.config import Settings
from app.breezy_form import BreezyJobRef, breezy_ref_for_job, fetch_breezy_form
from app.canadian_gov_portals import (
    GatineauCareersJobRef,
    GCJobsJobRef,
    OPSCareersJobRef,
    OttawaCareersJobRef,
    QuebecEmploiJobRef,
    fetch_canadian_gov_form,
    parse_gatineau_ref,
    parse_gc_jobs_ref,
    parse_ops_ref,
    parse_ottawa_ref,
    parse_quebec_emploi_ref,
)
from app.greenhouse_browser import (
    DomSnapshot,
    form_from_dom,
    inspect_hosted_form,
    inspect_page,
    reconcile,
)
from app.greenhouse_form import (
    FormFetchError,
    GreenhouseJobRef,
    RealForm,
    fetch_greenhouse_form,
    ref_for_job,
)
from app.lever_form import LeverJobRef, fetch_lever_form, lever_ref_for_job
from app.recruitee_form import RecruiteeJobRef, fetch_recruitee_form, recruitee_ref_for_job
from app.smartrecruiters_form import (
    SmartRecruitersJobRef,
    fetch_smartrecruiters_form,
    smartrecruiters_ref_for_job,
)
from app.workable_form import WorkableJobRef, fetch_workable_form, workable_ref_for_job
from app.workday_form import (
    WorkdayJobRef,
    fetch_workday_form,
    workday_ref_for_job,
)

JobRef = (
    GreenhouseJobRef
    | LeverJobRef
    | AshbyJobRef
    | SmartRecruitersJobRef
    | WorkdayJobRef
    | WorkableJobRef
    | BreezyJobRef
    | RecruiteeJobRef
    | GCJobsJobRef
    | OttawaCareersJobRef
    | OPSCareersJobRef
    | GatineauCareersJobRef
    | QuebecEmploiJobRef
)
SUPPORTED_PLATFORMS = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workday",
    "workable",
    "breezy",
    "recruitee",
    "gc_jobs",
    "ottawa_careers",
    "ops_careers",
    "gatineau_careers",
    "quebec_emploi",
)


class FormProvider(Protocol):
    def fetch(self, job: Any) -> RealForm: ...


def default_inspector(ref: JobRef, timeout_ms: int, trace_dir: str | None = None) -> DomSnapshot:
    """Read-only page inspection for a posting's hosted application form."""
    if isinstance(ref, GreenhouseJobRef):
        return inspect_hosted_form(ref, timeout_ms=timeout_ms, trace_dir=trace_dir)
    if isinstance(ref, LeverJobRef):
        return inspect_page(ref.apply_url, timeout_ms=timeout_ms, trace_dir=trace_dir)
    if isinstance(ref, SmartRecruitersJobRef):
        return inspect_page(ref.apply_url, timeout_ms=timeout_ms, trace_dir=trace_dir)
    if isinstance(ref, WorkdayJobRef):
        return inspect_page(ref.apply_url, timeout_ms=timeout_ms, trace_dir=trace_dir)
    if isinstance(ref, (WorkableJobRef, BreezyJobRef, RecruiteeJobRef, GCJobsJobRef, OttawaCareersJobRef, OPSCareersJobRef, GatineauCareersJobRef, QuebecEmploiJobRef)):
        return inspect_page(ref.apply_url, timeout_ms=timeout_ms, trace_dir=trace_dir)
    return inspect_page(ref.page_url, timeout_ms=timeout_ms, graphql_ops=ASHBY_READ_OPS, trace_dir=trace_dir)


class LiveFormProvider:
    """Fetch forms from live postings (read-only)."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        inspector: Callable[[Any], DomSnapshot] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        timeout_ms = int(settings.greenhouse_browser_timeout * 1000)
        trace_dir = settings.browser_trace_dir or None
        self.inspector = inspector or (lambda ref: default_inspector(ref, timeout_ms, trace_dir))

    def fetch(self, job: Any) -> RealForm:
        platform = platform_for_job(job)
        if platform == "greenhouse":
            return self._greenhouse(job)
        if platform == "lever":
            return self._lever(job)
        if platform == "ashby":
            return self._ashby(job)
        if platform == "smartrecruiters":
            return self._smartrecruiters(job)
        if platform == "workday":
            return self._workday(job)
        if platform == "workable":
            return self._workable(job)
        if platform == "breezy":
            return self._breezy(job)
        if platform == "recruitee":
            return self._recruitee(job)
        if platform in ("gc_jobs", "ottawa_careers", "ops_careers", "gatineau_careers", "quebec_emploi"):
            return self._canadian_gov(job, platform)
        raise FormFetchError(
            "form_unavailable",
            f"no real-form fetcher for platform '{platform}' yet; dry-run refused rather than planning against a sample form",
        )

    def _verify(self, form: RealForm, ref: JobRef, note: str = "") -> RealForm:
        setting = f"{form.platform.upper()}_BROWSER_VERIFY"
        enabled = bool(getattr(self.settings, setting.lower(), False))
        if not enabled:
            form.metadata["dom_verified"] = False
            form.warnings.append(f"not cross-checked against the rendered page ({setting}=false){note}")
            return form
        try:
            snapshot = self.inspector(ref)
        except FormFetchError as exc:
            raise FormFetchError("form_fetch_failed", f"browser verification is enabled but failed: {exc.detail}") from exc
        return reconcile(form, snapshot)

    def _greenhouse(self, job: Any) -> RealForm:
        ref = ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the Greenhouse board token and job id for this posting")
        try:
            form = fetch_greenhouse_form(ref, client=self.client, timeout=self.settings.greenhouse_form_timeout)
        except FormFetchError as exc:
            if exc.reason_code == "form_fetch_failed" and self.settings.greenhouse_browser_fallback:
                form = form_from_dom(ref, self.inspector(ref))
                form.warnings.insert(0, f"Greenhouse API fetch failed ({exc.detail}); form read from the rendered page")
                return form
            raise
        return self._verify(
            form, ref,
            "; some boards require fields the API omits, such as employment/education history or a phone-country picker",
        )

    def _lever(self, job: Any) -> RealForm:
        ref = lever_ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the Lever company and posting id for this job")
        form = fetch_lever_form(ref, job_location=getattr(job, "location", "") or "", client=self.client,
                                timeout=self.settings.greenhouse_form_timeout)
        return self._verify(form, ref)

    def _ashby(self, job: Any) -> RealForm:
        ref = ashby_ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the Ashby organization and posting id for this job")
        form = fetch_ashby_form(ref, job_location=getattr(job, "location", "") or "", client=self.client,
                                timeout=self.settings.greenhouse_form_timeout)
        return self._verify(form, ref)

    def _smartrecruiters(self, job: Any) -> RealForm:
        ref = smartrecruiters_ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the SmartRecruiters company and posting id for this job")
        form = fetch_smartrecruiters_form(ref, job_location=getattr(job, "location", "") or "", client=self.client,
                                         timeout=self.settings.greenhouse_form_timeout)
        return self._verify(form, ref)

    def _workday(self, job: Any) -> RealForm:
        ref = workday_ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the Workday tenant and posting id for this job")
        form = fetch_workday_form(ref, job_location=getattr(job, "location", "") or "", client=self.client,
                                  timeout=self.settings.greenhouse_form_timeout)
        return self._verify(form, ref)

    def _workable(self, job: Any) -> RealForm:
        ref = workable_ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the Workable company and shortcode for this job")
        form = fetch_workable_form(ref, client=self.client, timeout=self.settings.greenhouse_form_timeout)
        return self._verify(form, ref)

    def _breezy(self, job: Any) -> RealForm:
        ref = breezy_ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the Breezy company and position id for this job")
        form = fetch_breezy_form(ref, client=self.client, timeout=self.settings.greenhouse_form_timeout)
        return self._verify(form, ref)

    def _recruitee(self, job: Any) -> RealForm:
        ref = recruitee_ref_for_job(job)
        if ref is None:
            raise FormFetchError("form_unavailable", "could not identify the Recruitee company and offer slug for this job")
        form = fetch_recruitee_form(ref, client=self.client, timeout=self.settings.greenhouse_form_timeout)
        return self._verify(form, ref)

    def _canadian_gov(self, job: Any, platform: str) -> RealForm:
        url = getattr(job, "url", "") or ""
        job_id = getattr(job, "external_id", "") or "default"
        title = getattr(job, "title", "") or "Public Sector Role"
        if platform == "gc_jobs":
            ref = parse_gc_jobs_ref(url) or GCJobsJobRef(poster_id=job_id)
        elif platform == "ottawa_careers":
            ref = parse_ottawa_ref(url) or OttawaCareersJobRef(job_id=job_id)
        elif platform == "ops_careers":
            ref = parse_ops_ref(url) or OPSCareersJobRef(job_id=job_id)
        elif platform == "gatineau_careers":
            ref = parse_gatineau_ref(url) or GatineauCareersJobRef(position_id=job_id)
        else:
            ref = parse_quebec_emploi_ref(url) or QuebecEmploiJobRef(offer_id=job_id)

        form = fetch_canadian_gov_form(platform, ref, job_title=title, client=self.client)
        return self._verify(form, ref)
