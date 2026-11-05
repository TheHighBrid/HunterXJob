"""HunterXJob Telemetry and Execution Adapter.

Bridges HunterXJob application workflows (AutonomousRunner, Playwright form filler)
with the Autonomous Improvement Kernel's GenAI span tracing and continuous evaluation loop.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from improvement_kernel.runtime.runner import ImprovementRuntime, RunContext
from improvement_kernel.storage.db import get_current_champion

logger = logging.getLogger("improvement_kernel.adapters.hunterxjob")


class HunterXJobAdapter:
    """Connects HunterXJob live/dry-run jobs to the improvement runtime."""

    def __init__(self, session: Session, project: str = "hunterxjob") -> None:
        self.session = session
        self.project = project
        self.runtime = ImprovementRuntime(session=session, project=project)

    def start_application_run(
        self,
        job_id: str,
        job_title: str,
        company: str,
        platform: str,
        is_dry_run: bool = True,
        extra_inputs: dict[str, Any] | None = None,
    ) -> RunContext:
        """Start a traced application run with active champion version."""
        champion = get_current_champion(self.session, self.project)
        inputs = {
            "job_id": job_id,
            "job_title": job_title,
            "company": company,
            "platform": platform,
            "is_dry_run": is_dry_run,
        }
        if extra_inputs:
            inputs.update(extra_inputs)

        return self.runtime.start_run(
            task_type=f"apply_{platform}",
            agent_version=champion.version,
            inputs=inputs,
        )

    def trace_field_fill(
        self,
        ctx: RunContext,
        field_name: str,
        selector_used: str,
        value: str,
        success: bool = True,
        error_msg: str | None = None,
    ) -> None:
        """Record an execute_tool span for a field filling action."""
        with ctx.span("execute_tool", f"fill_{field_name}") as span:
            span.set_attributes({
                "field_name": field_name,
                "selector_used": selector_used,
                "value_length": len(value) if value else 0,
                "success": success,
            })
            if not success or error_msg:
                span.record_error("FIELD_FILL_ERROR", error_msg or f"Failed to fill {field_name}")

    def trace_truth_guard(
        self,
        ctx: RunContext,
        question: str,
        selected_answer: str,
        is_verified: bool,
        is_violation: bool = False,
    ) -> None:
        """Record a verify_guardrail span for truth guard / credential verification."""
        with ctx.span("verify_guardrail", "truth_guard") as span:
            span.set_attributes({
                "question": question,
                "selected_answer": selected_answer,
                "is_verified_in_profile": is_verified,
                "is_violation": is_violation,
            })
            if is_violation:
                span.record_error(
                    "TRUTH_GUARD_VIOLATION",
                    f"Selected unverified answer '{selected_answer}' for '{question}'",
                )

    def complete_application_run(
        self,
        ctx: RunContext,
        success: bool,
        fields_filled_ratio: float,
        is_dry_run: bool = True,
        receipt_hash: str | None = None,
        confirmation_screenshot_uri: str | None = None,
        error_msg: str | None = None,
    ) -> None:
        """Finalize the application run and emit outputs for Tier 1-3 grading."""
        outputs = {
            "submission_outcome": "success" if success else "failed",
            "fields_filled_ratio": fields_filled_ratio,
            "is_dry_run": is_dry_run,
            "receipt_hash": receipt_hash,
            "confirmation_screenshot_uri": confirmation_screenshot_uri,
        }
        if confirmation_screenshot_uri:
            ctx.add_artifact(confirmation_screenshot_uri)

        if not success:
            ctx.fail_run("SUBMISSION_NOT_CONFIRMED", error_msg or "Application submission not confirmed")
        else:
            ctx.set_output(outputs)
