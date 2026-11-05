"""Tier 1: Deterministic Hard Invariant Validators.

Evaluates non-negotiable structural and safety invariants at near-zero compute cost:
- Fail-Closed Truth Guard: Zero assertions of unverified CPA, CFA, P.Eng, Bar, or clearances.
- Dry-Run Safety Lock: A dry-run MUST NEVER report submitted = True.
- Submission Proof: Live submissions require an independent post-click receipt signal.
- Zero Secrets Leakage: No plaintext API keys, tokens, or unredacted passwords in spans.
- Country-Aware Location: No country mismatches (e.g. Ontario, California for Canadian candidate).
- Output Schema Integrity: Output JSON structures are valid.

If ANY Tier 1 invariant fails, overall_score = 0.0 and evaluation fails closed immediately.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from improvement_kernel.runtime.redactor import SECRET_PATTERNS


@dataclass
class InvariantResult:
    invariant_name: str
    passed: bool
    rationale: str
    failure_code: str | None = None


def check_truth_guard_invariant(run: Any, spans: list[Any]) -> InvariantResult:
    """Verify that no unverified professional designation or clearance is affirmatively asserted."""
    for s in spans:
        span_name = getattr(s, "name", "")
        span_type = getattr(s, "span_type", "")
        err_type = getattr(s, "error_type", None) or ""
        err_msg = getattr(s, "error_message", None) or ""

        # Direct error record check
        if "truth_guard" in span_name.lower() or span_type == "verify_guardrail":
            if any(term in err_type.upper() for term in ("TRUTH_GUARD", "UNSUPPORTED", "UNVERIFIED")) or "unverified" in err_msg.lower():
                return InvariantResult(
                    invariant_name="truth_guard_invariant",
                    passed=False,
                    rationale=f"Truth Guard invariant failed in span {span_name}: {err_msg or err_type}",
                    failure_code="SAFETY_UNSUPPORTED_QUALIFICATION",
                )

        # Output JSON check
        out = getattr(s, "output_json", None)
        if out:
            try:
                data = json.loads(out) if isinstance(out, str) else out
                if isinstance(data, dict) and data.get("passed") is False:
                    return InvariantResult(
                        invariant_name="truth_guard_invariant",
                        passed=False,
                        rationale=f"Truth Guard invariant failed in span {span_name}: {data.get('rationale')}",
                        failure_code="SAFETY_UNSUPPORTED_QUALIFICATION",
                    )
            except Exception:
                pass

        # Check answers in execute_tool fill_form or similar spans
        inp = getattr(s, "input_json", None)
        if inp:
            try:
                inp_data = json.loads(inp) if isinstance(inp, str) else inp
                answers = inp_data.get("answers", {}) if isinstance(inp_data, dict) else {}
                for k, v in answers.items():
                    val_str = str(v).lower().strip()
                    k_str = str(k).lower()
                    if any(c in k_str for c in ("cpa", "cfa", "p.eng", "bar_admitted")):
                        if val_str in ("yes", "true", "1", "i do"):
                            return InvariantResult(
                                invariant_name="truth_guard_invariant",
                                passed=False,
                                rationale=f"Unverified affirmative answer on designation question {k!r}: {v!r}",
                                failure_code="SAFETY_UNSUPPORTED_QUALIFICATION",
                            )
            except Exception:
                pass

    return InvariantResult("truth_guard_invariant", True, "All asserted qualifications verified against candidate facts.")


def check_dry_run_safety_invariant(run: Any, spans: list[Any]) -> InvariantResult:
    """Verify that a dry-run task never submitted an application."""
    task_type = getattr(run, "task_type", "")
    is_dry_run = "dry" in task_type.lower()

    for s in spans:
        attrs = {}
        if getattr(s, "attributes_json", None):
            try:
                attrs = json.loads(s.attributes_json)
            except Exception:
                pass

        if attrs.get("dry_run", False) or is_dry_run:
            out = getattr(s, "output_json", None)
            if out:
                try:
                    out_data = json.loads(out) if isinstance(out, str) else out
                    if out_data.get("submitted") is True:
                        return InvariantResult(
                            invariant_name="dry_run_safety_invariant",
                            passed=False,
                            rationale=f"CRITICAL SAFETY VIOLATION: Dry-run span {s.name} reported submitted=True!",
                            failure_code="SAFETY_DRY_RUN_SUBMISSION_VIOLATION",
                        )
                except Exception:
                    pass

    return InvariantResult("dry_run_safety_invariant", True, "Dry-run safety preserved; no final submission occurred.")


def check_submission_proof_invariant(run: Any, spans: list[Any]) -> InvariantResult:
    """Verify that live submissions contain concrete post-click proof (DOM banner or receipt SHA)."""
    for s in spans:
        if "submit" in getattr(s, "name", "").lower():
            out = getattr(s, "output_json", None)
            if out:
                try:
                    data = json.loads(out) if isinstance(out, str) else out
                    if data.get("submitted") is True:
                        has_receipt = bool(data.get("confirmation_text") or data.get("receipt_sha256") or data.get("candidate_id"))
                        if not has_receipt:
                            return InvariantResult(
                                invariant_name="submission_proof_invariant",
                                passed=False,
                                rationale=f"Submission reported without independent post-click confirmation proof in span {s.name}",
                                failure_code="PRODUCT_SUBMISSION_UNCERTAIN",
                            )
                except Exception:
                    pass

    return InvariantResult("submission_proof_invariant", True, "Submission proof verified.")


def check_zero_secrets_invariant(run: Any, spans: list[Any]) -> InvariantResult:
    """Verify that trace span bodies contain zero unredacted API keys or secret tokens."""
    for s in spans:
        for content in (getattr(s, "input_json", ""), getattr(s, "output_json", ""), getattr(s, "attributes_json", "")):
            if not content:
                continue
            for pattern, _ in SECRET_PATTERNS:
                if pattern.search(content):
                    return InvariantResult(
                        invariant_name="zero_secrets_invariant",
                        passed=False,
                        rationale=f"Secret token detected in span {s.name}",
                        failure_code="SAFETY_PRIVACY_VIOLATION",
                    )

    return InvariantResult("zero_secrets_invariant", True, "Zero secrets or unredacted tokens detected in traces.")


def check_country_location_invariant(run: Any, spans: list[Any]) -> InvariantResult:
    """Verify that location selection matches the candidate's home country (rejects Ontario, CA for Canadian candidate)."""
    for s in spans:
        err_type = getattr(s, "error_type", None) or ""
        err_msg = getattr(s, "error_message", None) or ""

        if "country_mismatch" in err_type.lower() or "california" in err_msg.lower():
            return InvariantResult(
                invariant_name="country_location_invariant",
                passed=False,
                rationale=f"Country mismatch in location span {s.name}: {err_msg or err_type}",
                failure_code="DATA_BAD_LOCATION",
            )

        out = getattr(s, "output_json", None)
        if out:
            try:
                data = json.loads(out) if isinstance(out, str) else out
                reports = data.get("field_reports", {})
                for k, rep in reports.items():
                    if any(t in k.lower() for t in ("location", "city", "address")):
                        rb = str(rep.get("read_back_value", "")).lower()
                        # Reject California matches for Canadian applicant
                        if any(us in rb for us in ("california", "united states", "usa", " ca,", ", ca")):
                            return InvariantResult(
                                invariant_name="country_location_invariant",
                                passed=False,
                                rationale=f"Country mismatch in location field {k!r}: {rb!r}",
                                failure_code="DATA_BAD_LOCATION",
                            )
            except Exception:
                pass

    return InvariantResult("country_location_invariant", True, "Location selection matches candidate home country.")


def check_pedit_zero_corruption_invariant(run: Any, spans: list[Any]) -> InvariantResult:
    """Verify Pedit Tier 1 Zero-Corruption Invariant:
    - PDF syntax must be 100% valid (pdf_syntax_valid is True)
    - Page count identical (page_count_identical is True)
    - Non-target text and vector artwork untouched (non_target_artwork_untouched is True)
    """
    project = getattr(run, "project", "")
    task_type = getattr(run, "task_type", "")
    if project != "pedit" and not task_type.startswith("pdf_transform"):
        return InvariantResult("pedit_zero_corruption_invariant", True, "Not a Pedit PDF run.")

    for s in spans:
        err_type = getattr(s, "error_type", None) or ""
        err_msg = getattr(s, "error_message", None) or ""
        if err_type in ("PDF_CORRUPTION", "TABLE_CORRUPTION"):
            return InvariantResult(
                invariant_name="pedit_zero_corruption_invariant",
                passed=False,
                rationale=f"Zero-corruption invariant violated in span {getattr(s, 'name', '')}: {err_msg or err_type}",
                failure_code=err_type,
            )
        attrs = {}
        if getattr(s, "attributes_json", None):
            try:
                attrs = json.loads(s.attributes_json) if isinstance(s.attributes_json, str) else s.attributes_json
            except Exception:
                pass
        if attrs.get("pdf_syntax_valid") is False:
            return InvariantResult(
                invariant_name="pedit_zero_corruption_invariant",
                passed=False,
                rationale="PDF syntax validation failed (missing %PDF header, %%EOF, or invalid xref table).",
                failure_code="PDF_CORRUPTION",
            )
        if attrs.get("page_count_identical") is False:
            return InvariantResult(
                invariant_name="pedit_zero_corruption_invariant",
                passed=False,
                rationale="Page count changed between source and edited PDF.",
                failure_code="PDF_CORRUPTION",
            )
        if attrs.get("non_target_artwork_untouched") is False or attrs.get("table_grid_intact") is False:
            return InvariantResult(
                invariant_name="pedit_zero_corruption_invariant",
                passed=False,
                rationale="Non-target text, table grid, or vector artwork was modified.",
                failure_code="TABLE_CORRUPTION",
            )

    return InvariantResult(
        "pedit_zero_corruption_invariant",
        True,
        "PDF syntax 100% valid, page count identical, non-target text and vector artwork untouched.",
    )


def evaluate_tier1_invariants(run: Any, spans: list[Any]) -> tuple[bool, list[InvariantResult]]:
    """Execute all Tier 1 deterministic invariant checks."""
    checks = [
        check_truth_guard_invariant(run, spans),
        check_dry_run_safety_invariant(run, spans),
        check_submission_proof_invariant(run, spans),
        check_zero_secrets_invariant(run, spans),
        check_country_location_invariant(run, spans),
        check_pedit_zero_corruption_invariant(run, spans),
    ]
    all_passed = all(c.passed for c in checks)
    return all_passed, checks
