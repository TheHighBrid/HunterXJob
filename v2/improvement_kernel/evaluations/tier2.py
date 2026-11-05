"""Tier 2: Domain-Specific Quantitative Metric Graders.

Computes operational and physical domain metrics for HunterXJob:
- Field extraction & fill accuracy: (fields_filled / fields_attempted)
- Unmatched fields penalty
- Duplicate avoidance: 1.0 if no duplicate application attempt
- File attachment rate: required resume/CV properly attached
- Latency budget compliance: latency <= 30s
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Tier2MetricResult:
    score: float
    metrics: dict[str, float]
    passed: bool
    rationale: str


def evaluate_tier2_metrics(run: Any, spans: list[Any]) -> Tier2MetricResult:
    """Compute quantitative domain metrics across all run spans."""
    project = getattr(run, "project", "")
    task_type = getattr(run, "task_type", "")
    if project == "pedit" or task_type.startswith("pdf_transform"):
        max_overflow_pts = 0.0
        max_baseline_drift_pts = 0.0
        max_font_distance = 0.0
        max_kerning_delta = 0.0
        ocr_acc = 1.0
        for s in spans:
            attrs = {}
            if getattr(s, "attributes_json", None):
                try:
                    attrs = json.loads(s.attributes_json) if isinstance(s.attributes_json, str) else s.attributes_json
                except Exception:
                    pass
            if "bbox_overflow_pts" in attrs:
                max_overflow_pts = max(max_overflow_pts, float(attrs["bbox_overflow_pts"]))
            elif "bounding_box_drift_pts" in attrs:
                max_overflow_pts = max(max_overflow_pts, float(attrs["bounding_box_drift_pts"]))
            if "baseline_drift_pts" in attrs:
                max_baseline_drift_pts = max(max_baseline_drift_pts, abs(float(attrs["baseline_drift_pts"])))
            if "font_match_distance" in attrs:
                max_font_distance = max(max_font_distance, float(attrs["font_match_distance"]))
            if "kerning_delta_pt" in attrs:
                max_kerning_delta = max(max_kerning_delta, abs(float(attrs["kerning_delta_pt"])))
            if "character_accuracy" in attrs:
                ocr_acc = min(ocr_acc, float(attrs["character_accuracy"]))

        # Spatial alignment fidelity invariant: bounding box text overflow <= 0.5pt, baseline drift == 0
        overflow_score = 1.0 if max_overflow_pts <= 0.5 else max(0.0, 1.0 - (max_overflow_pts - 0.5) / 2.0)
        baseline_score = 1.0 if max_baseline_drift_pts == 0.0 else max(0.0, 1.0 - max_baseline_drift_pts / 0.5)
        font_score = max(0.0, 1.0 - max_font_distance)
        composite = round(0.45 * overflow_score + 0.35 * baseline_score + 0.10 * font_score + 0.10 * ocr_acc, 4)
        passed = (max_overflow_pts <= 0.5) and (max_baseline_drift_pts == 0.0) and (ocr_acc >= 0.98)
        metrics = {
            "bbox_overflow_pts": round(max_overflow_pts, 4),
            "baseline_drift_pts": round(max_baseline_drift_pts, 5),
            "font_match_distance": round(max_font_distance, 4),
            "kerning_delta_pt": round(max_kerning_delta, 5),
            "character_accuracy": round(ocr_acc, 4),
            "spatial_alignment_score": round(composite, 4),
        }
        rationale = (
            f"Pedit Spatial Alignment: bbox_overflow={max_overflow_pts:.3f}pt (<=0.5pt), "
            f"baseline_drift={max_baseline_drift_pts:.4f}pt (=0pt), "
            f"font_match_distance={max_font_distance:.3f}"
        )
        return Tier2MetricResult(score=composite, metrics=metrics, passed=passed, rationale=rationale)

    fields_attempted = 0
    fields_filled = 0
    unmatched_count = 0
    files_attached = 0
    duplicate_avoidance = 1.0
    latency_ms = getattr(run, "latency_ms", 0)

    for s in spans:
        span_name = getattr(s, "name", "").lower()
        out = getattr(s, "output_json", None)
        if out:
            try:
                data = json.loads(out) if isinstance(out, str) else out
                if "field_reports" in data or "fields_attempted" in data:
                    fa = int(data.get("fields_attempted", 0))
                    ff = int(data.get("fields_filled", 0))
                    uf = list(data.get("unmatched_fields", []))
                    att = list(data.get("files_attached", []))

                    fields_attempted = max(fields_attempted, fa)
                    fields_filled = max(fields_filled, ff)
                    unmatched_count = max(unmatched_count, len(uf))
                    files_attached = max(files_attached, len(att))

                if "duplicate" in span_name:
                    if data.get("is_duplicate") and not data.get("blocked"):
                        duplicate_avoidance = 0.0
            except Exception:
                pass

    # 1. Fill Accuracy Metric
    fill_ratio = (fields_filled / fields_attempted) if fields_attempted > 0 else 1.0

    # 2. Latency compliance Metric (target <= 30000ms)
    latency_score = 1.0 if latency_ms <= 30000 else max(0.0, 1.0 - (latency_ms - 30000) / 30000)

    # 3. File attachment Metric
    file_score = 1.0 if files_attached > 0 or fields_attempted == 0 else 0.8

    # Composite weighted Tier 2 Score
    composite_score = (
        0.50 * fill_ratio +
        0.20 * duplicate_avoidance +
        0.15 * file_score +
        0.15 * latency_score
    )

    passed = fill_ratio >= 0.85 and duplicate_avoidance == 1.0

    metrics = {
        "fields_attempted": float(fields_attempted),
        "fields_filled": float(fields_filled),
        "unmatched_count": float(unmatched_count),
        "fill_ratio": float(fill_ratio),
        "files_attached": float(files_attached),
        "duplicate_avoidance": float(duplicate_avoidance),
        "latency_score": float(latency_score),
    }

    rationale = (
        f"Fill ratio: {fill_ratio:.1%} ({fields_filled}/{fields_attempted}), "
        f"Unmatched: {unmatched_count}, Duplicate avoidance: {duplicate_avoidance}, "
        f"Latency: {latency_ms}ms"
    )

    return Tier2MetricResult(
        score=round(composite_score, 4),
        metrics=metrics,
        passed=passed,
        rationale=rationale,
    )
