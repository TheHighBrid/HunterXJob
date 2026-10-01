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

JobRef = GreenhouseJobRef | LeverJobRef | AshbyJobRef
SUPPORTED_PLATFORMS = ("greenhouse", "lever", "ashby")


class FormProvider(Protocol):
    def fetch(self, job: Any) -> RealForm: ...


def default_inspector(ref: JobRef, timeout_ms: int) -> DomSnapshot:
    """Read-only page inspection for a posting's hosted application form."""
    if isinstance(ref, GreenhouseJobRef):
        return inspect_hosted_form(ref, timeout_ms=timeout_ms)
    if isinstance(ref, LeverJobRef):
        return inspect_page(ref.apply_url, timeout_ms=timeout_ms)
    return inspect_page(ref.page_url, timeout_ms=timeout_ms, graphql_ops=ASHBY_READ_OPS)


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
        self.inspector = inspector or (lambda ref: default_inspector(ref, timeout_ms))

    def fetch(self, job: Any) -> RealForm:
        platform = platform_for_job(job)
        if platform == "greenhouse":
            return self._greenhouse(job)
        if platform == "lever":
            return self._lever(job)
        if platform == "ashby":
            return self._ashby(job)
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
