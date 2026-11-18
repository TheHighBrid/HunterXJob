"""Causal root-cause analysis via span tree traversal.

Traverses the hierarchical OpenTelemetry GenAI span tree to separate
upstream root causes from downstream symptoms.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from improvement_kernel.clustering.taxonomy import classify_failure


@dataclass
class CausalDiagnosis:
    failure_category: str
    failure_code: str
    root_cause_span_id: str
    root_cause_span_name: str
    error_message: str
    pattern_signature: str
    diagnostic_target: str
    details: dict[str, Any]


def analyze_causal_root_cause(run: Any, spans: list[Any]) -> CausalDiagnosis:
    """Traverse span tree to find the earliest ancestor failure."""
    if not spans:
        cat, code = classify_failure(getattr(run, "error_message", None))
        sig = f"{cat}::{code}::root"
        return CausalDiagnosis(
            failure_category=cat,
            failure_code=code,
            root_cause_span_id=getattr(run, "id", "unknown"),
            root_cause_span_name="invoke_agent",
            error_message=str(getattr(run, "error_message", "") or "Unknown error"),
            pattern_signature=sig,
            diagnostic_target="system_prompts",
            details={},
        )

    # 1. Sort spans chronologically
    sorted_spans = sorted(spans, key=lambda s: getattr(s, "created_at", 0))

    # 2. Find the earliest span with an explicit error or guardrail failure
    earliest_failing_span = None
    for s in sorted_spans:
        err = getattr(s, "error_message", None) or getattr(s, "error_type", None)
        out = getattr(s, "output_json", None)
        is_guardrail_fail = False
        if out and "verify_guardrail" in getattr(s, "name", ""):
            try:
                data = json.loads(out) if isinstance(out, str) else out
                if not data.get("passed", True):
                    is_guardrail_fail = True
            except Exception:
                pass

        if err or is_guardrail_fail:
            earliest_failing_span = s
            break

    if not earliest_failing_span:
        # Fall back to root or last span
        earliest_failing_span = sorted_spans[0]

    err_msg = (
        getattr(earliest_failing_span, "error_message", None)
        or getattr(run, "error_message", None)
        or f"Failure in {earliest_failing_span.name}"
    )

    cat, code = classify_failure(err_msg)

    # Diagnostic target in harness
    if getattr(run, "project", "") == "fidelis":
        content_type = "image"
        for s in (earliest_failing_span, sorted_spans[0]):
            for attr_f in ("input_json", "attributes_json"):
                raw = getattr(s, attr_f, None)
                if raw:
                    try:
                        d = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(d, dict) and "content_type" in d:
                            content_type = d["content_type"]
                            break
                    except Exception:
                        pass
        if "portrait" in err_msg.lower():
            content_type = "low_light_portrait"
        elif "text" in err_msg.lower() or "glyph" in err_msg.lower():
            content_type = "text_graphics"

        sig = f"{cat}::{code}::{content_type}"
        diagnostic_target = "routing_rules.yaml" if any(k in code for k in ("SKIN", "TEXT", "RINGING", "COLOR")) else "tile_config.yaml"
    elif "selector" in code.lower() or "unmatched" in code.lower():
        diagnostic_target = "selectors.yaml"
    elif "credential" in code.lower() or "truth" in code.lower():
        diagnostic_target = "answer_policies.yaml"
    elif "location" in code.lower():
        diagnostic_target = "selectors.yaml"
    elif "format" in code.lower() or "claim" in code.lower():
        diagnostic_target = "prompt_templates.yaml"
    else:
        diagnostic_target = "harness_config"

    # Signature for clustering
    if getattr(run, "project", "") != "fidelis":
        clean_span_name = earliest_failing_span.name.split()[0] if earliest_failing_span.name else "tool"
        sig = f"{cat}::{code}::{clean_span_name}"

    details = {
        "span_name": earliest_failing_span.name,
        "span_type": getattr(earliest_failing_span, "span_type", "execute_tool"),
        "error_type": getattr(earliest_failing_span, "error_type", None),
        "input": getattr(earliest_failing_span, "input_json", None),
    }

    return CausalDiagnosis(
        failure_category=cat,
        failure_code=code,
        root_cause_span_id=earliest_failing_span.id,
        root_cause_span_name=earliest_failing_span.name,
        error_message=err_msg,
        pattern_signature=sig,
        diagnostic_target=diagnostic_target,
        details=details,
    )
