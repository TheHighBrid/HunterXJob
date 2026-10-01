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
from app.config import Settings
from app.greenhouse_browser import (
    DomSnapshot,
    form_from_dom,
    inspect_hosted_form,
    reconcile,
)
from app.greenhouse_form import (
    FormFetchError,
    GreenhouseJobRef,
    RealForm,
    fetch_greenhouse_form,
    ref_for_job,
)


class FormProvider(Protocol):
    def fetch(self, job: Any) -> RealForm: ...


class LiveFormProvider:
    """Fetch forms from live postings (read-only)."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        inspector: Callable[[GreenhouseJobRef], DomSnapshot] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.inspector = inspector or (lambda ref: inspect_hosted_form(ref, timeout_ms=int(settings.greenhouse_browser_timeout * 1000)))

    def fetch(self, job: Any) -> RealForm:
        platform = platform_for_job(job)
        if platform != "greenhouse":
            raise FormFetchError(
                "form_unavailable",
                f"no real-form fetcher for platform '{platform}' yet; dry-run refused rather than planning against a sample form",
            )
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
        if self.settings.greenhouse_browser_verify:
            try:
                snapshot = self.inspector(ref)
            except FormFetchError as exc:
                raise FormFetchError("form_fetch_failed", f"browser verification is enabled but failed: {exc.detail}") from exc
            form = reconcile(form, snapshot)
        else:
            form.metadata["dom_verified"] = False
            form.warnings.append(
                "not cross-checked against the rendered page (GREENHOUSE_BROWSER_VERIFY=false); some boards require "
                "fields the API omits, such as employment/education history or a phone-country picker"
            )
        return form
