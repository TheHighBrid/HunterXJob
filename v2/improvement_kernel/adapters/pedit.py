"""Pedit Precision PDF Transformation Telemetry Adapter.

Connects Pedit PDF precision modifications to the Autonomous Improvement Kernel.
Evaluates:
- Layout and coordinate bounding-box preservation
- Font embedding, kerning, and baseline alignment
- Table structure & tabular cell flow integrity
- OCR text alignment and PDF/A metadata compliance
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from improvement_kernel.runtime.runner import ImprovementRuntime, RunContext
from improvement_kernel.storage.db import get_current_champion

logger = logging.getLogger("improvement_kernel.adapters.pedit")


class PeditAdapter:
    """Connects Pedit precision document transformations to the improvement runtime."""

    def __init__(self, session: Session, project: str = "pedit") -> None:
        self.session = session
        self.project = project
        self.runtime = ImprovementRuntime(session=session, project=project)

    def start_edit_run(
        self,
        doc_id: str,
        operation: str,
        page_count: int,
        input_sha256: str,
        extra_inputs: dict[str, Any] | None = None,
    ):
        """Start a traced PDF precision edit run with active champion version."""
        champion = get_current_champion(self.session, self.project)
        inputs = {
            "doc_id": doc_id,
            "operation": operation,
            "page_count": page_count,
            "input_sha256": input_sha256,
        }
        if extra_inputs:
            inputs.update(extra_inputs)

        return self.runtime.start_run(
            task_type=f"pdf_transform_{operation}",
            agent_version=champion.version,
            inputs=inputs,
        )

    def trace_reembed_font(
        self,
        ctx: RunContext,
        page_num: int,
        requested_font: str,
        resolved_font: str,
        font_match_distance: float,
        embedded_bytes: int = 0,
    ) -> None:
        """Record reembed_font operation span with font matching distance."""
        with ctx.span("reembed_font", f"page_{page_num}_{requested_font}") as span:
            span.set_attributes({
                "page_num": page_num,
                "requested_font": requested_font,
                "resolved_font": resolved_font,
                "font_match_distance": round(float(font_match_distance), 4),
                "font_matched": font_match_distance <= 0.15,
                "embedded_bytes": embedded_bytes,
            })
            if font_match_distance > 0.15:
                span.record_error(
                    "FONT_MISMATCH",
                    f"Font match distance {font_match_distance:.3f} exceeds 0.15 invariant for {requested_font}",
                )

    def trace_adjust_layout(
        self,
        ctx: RunContext,
        page_num: int,
        bbox_before: list[float] | tuple[float, ...],
        bbox_after: list[float] | tuple[float, ...],
        bbox_overflow_pts: float,
        baseline_drift_pts: float,
        kerning_delta_pt: float = 0.0,
        tracking_pt: float = 0.0,
        spacing_mode: str = "measured uniform tracking",
        word_wrapped: bool = False,
    ) -> None:
        """Record adjust_layout operation span with bbox coordinates before/after, overflow, baseline drift, and kerning deltas."""
        with ctx.span("adjust_layout", f"page_{page_num}") as span:
            span.set_attributes({
                "page_num": page_num,
                "bbox_before": list(bbox_before),
                "bbox_after": list(bbox_after),
                "bbox_overflow_pts": round(float(bbox_overflow_pts), 4),
                "baseline_drift_pts": round(float(baseline_drift_pts), 5),
                "bounding_box_drift_pts": round(float(max(bbox_overflow_pts, baseline_drift_pts)), 4),
                "kerning_delta_pt": round(float(kerning_delta_pt), 5),
                "tracking_pt": round(float(tracking_pt), 5),
                "spacing_mode": spacing_mode,
                "word_wrapped": word_wrapped,
            })
            if bbox_overflow_pts > 0.5:
                span.record_error(
                    "BOUNDING_BOX_OVERFLOW",
                    f"Bounding box text overflow {bbox_overflow_pts:.3f}pt exceeds 0.5pt spatial alignment invariant",
                )
            if abs(baseline_drift_pts) > 0.0:
                span.record_error(
                    "LAYOUT_DRIFT",
                    f"Baseline drift {baseline_drift_pts:.4f}pt violates zero-drift invariant",
                )

    def trace_replace_text(
        self,
        ctx: RunContext,
        page_num: int,
        old_text: str,
        new_text: str,
        bbox_before: list[float] | tuple[float, ...],
        bbox_after: list[float] | tuple[float, ...],
        bbox_overflow_pts: float = 0.0,
        baseline_drift_pts: float = 0.0,
        font_match_distance: float = 0.0,
        kerning_delta_pt: float = 0.0,
        file_size_delta_bytes: int = 0,
        pdf_syntax_valid: bool = True,
    ) -> None:
        """Record replace_text operation span with full geometric, typographic, and integrity telemetry."""
        with ctx.span("replace_text", f"page_{page_num}") as span:
            span.set_attributes({
                "page_num": page_num,
                "old_text_len": len(old_text),
                "new_text_len": len(new_text),
                "bbox_before": list(bbox_before),
                "bbox_after": list(bbox_after),
                "bbox_overflow_pts": round(float(bbox_overflow_pts), 4),
                "baseline_drift_pts": round(float(baseline_drift_pts), 5),
                "font_match_distance": round(float(font_match_distance), 4),
                "kerning_delta_pt": round(float(kerning_delta_pt), 5),
                "file_size_delta_bytes": int(file_size_delta_bytes),
                "pdf_syntax_valid": bool(pdf_syntax_valid),
            })
            if not pdf_syntax_valid:
                span.record_error("PDF_CORRUPTION", "PDF syntax validation failed after replace_text")
            if bbox_overflow_pts > 0.5:
                span.record_error(
                    "BOUNDING_BOX_OVERFLOW",
                    f"replace_text overflow {bbox_overflow_pts:.3f}pt > 0.5pt limit",
                )

    def trace_zero_corruption_check(
        self,
        ctx: RunContext,
        pdf_syntax_valid: bool,
        page_count_identical: bool,
        non_target_artwork_untouched: bool,
        file_size_delta_bytes: int,
    ) -> None:
        """Record Tier 1 zero-corruption invariant check."""
        with ctx.span("zero_corruption_check", "document_integrity") as span:
            span.set_attributes({
                "pdf_syntax_valid": bool(pdf_syntax_valid),
                "page_count_identical": bool(page_count_identical),
                "non_target_artwork_untouched": bool(non_target_artwork_untouched),
                "file_size_delta_bytes": int(file_size_delta_bytes),
            })
            if not (pdf_syntax_valid and page_count_identical and non_target_artwork_untouched):
                span.record_error(
                    "PDF_CORRUPTION",
                    f"Zero-corruption invariant failed (syntax={pdf_syntax_valid}, pages={page_count_identical}, artwork={non_target_artwork_untouched})",
                )

    def trace_visual_rendering_300dpi(
        self,
        ctx: RunContext,
        page_num: int,
        unmasked_pixel_diff_fraction: float,
        dpi: int = 300,
    ) -> None:
        """Record Tier 3 300 DPI visual rendering comparison on non-target zones."""
        with ctx.span("visual_render_diff_300dpi", f"page_{page_num}") as span:
            span.set_attributes({
                "page_num": page_num,
                "dpi": dpi,
                "dpi_300_unmasked_pixel_diff": round(float(unmasked_pixel_diff_fraction), 6),
                "non_target_zones_intact": unmasked_pixel_diff_fraction == 0.0,
            })
            if unmasked_pixel_diff_fraction > 0.0:
                span.record_error(
                    "VISUAL_RENDER_REGRESSION",
                    f"300 DPI pixel diff on non-target zone = {unmasked_pixel_diff_fraction:.6%}",
                )

    def trace_layout_verification(
        self,
        ctx: RunContext,
        page_num: int,
        bounding_box_drift_pts: float,
        font_matched: bool,
        table_grid_intact: bool,
        bbox_overflow_pts: float | None = None,
        baseline_drift_pts: float = 0.0,
        font_match_distance: float = 0.0,
        kerning_delta_pt: float = 0.0,
    ) -> None:
        """Record precision layout check span."""
        overflow = bbox_overflow_pts if bbox_overflow_pts is not None else bounding_box_drift_pts
        with ctx.span("verify_layout", f"page_{page_num}") as span:
            span.set_attributes({
                "page_num": page_num,
                "bounding_box_drift_pts": bounding_box_drift_pts,
                "bbox_overflow_pts": round(float(overflow), 4),
                "baseline_drift_pts": round(float(baseline_drift_pts), 5),
                "font_matched": font_matched,
                "font_match_distance": round(float(font_match_distance), 4),
                "kerning_delta_pt": round(float(kerning_delta_pt), 5),
                "table_grid_intact": table_grid_intact,
            })
            if overflow > 0.5 or bounding_box_drift_pts > 0.5:
                span.record_error("LAYOUT_DRIFT", f"Text bounding box overflow/drift {max(overflow, bounding_box_drift_pts):.3f} pts (limit 0.5 pts)")
            if abs(baseline_drift_pts) > 0.0:
                span.record_error("LAYOUT_DRIFT", f"Baseline drift {baseline_drift_pts:.4f} pts violates 0pt invariant")
            if not table_grid_intact:
                span.record_error("TABLE_CORRUPTION", "Tabular column boundaries corrupted during operation")

    def trace_ocr_alignment(
        self,
        ctx: RunContext,
        page_num: int,
        character_accuracy: float,
        word_error_rate: float,
    ) -> None:
        """Record OCR bounding alignment check span."""
        with ctx.span("ocr_alignment", f"page_{page_num}") as span:
            span.set_attributes({
                "page_num": page_num,
                "character_accuracy": character_accuracy,
                "word_error_rate": word_error_rate,
            })
            if character_accuracy < 0.98:
                span.record_error("OCR_DEVIATION", f"OCR character accuracy {character_accuracy} below 98% invariant")

    def evaluate_pdf_trace(self, trace_payload: dict[str, Any]) -> dict[str, Any]:
        """Evaluate a PDF transformation trace against Tier 1, 2, and 3 invariants."""
        import uuid
        from improvement_kernel.storage.db import record_evaluation, record_run

        doc_id = str(trace_payload.get("doc_id") or f"doc_{uuid.uuid4().hex[:8]}")
        operation = str(trace_payload.get("operation", "replace_text"))
        metrics = dict(trace_payload.get("metrics", {}))
        champion = get_current_champion(self.session, self.project)

        # Tier 1 Invariant Checks
        pdf_valid = bool(metrics.get("pdf_valid", True))
        page_count_match = bool(metrics.get("page_count_match", True))
        non_target_untouched = bool(metrics.get("non_target_untouched", True))

        t1_checks = {
            "pdf_syntax_valid": {
                "passed": pdf_valid,
                "reason": "PDF syntax is valid" if pdf_valid else "Corrupted PDF header/cross-reference table",
            },
            "page_count_match": {
                "passed": page_count_match,
                "reason": "Page count preserved" if page_count_match else "Page count mutated during edit",
            },
            "non_target_untouched": {
                "passed": non_target_untouched,
                "reason": "Non-target text and graphics untouched" if non_target_untouched else "Collateral corruption in adjacent objects",
            },
        }
        t1_passed = all(c["passed"] for c in t1_checks.values())

        # Tier 2 Metrics
        bbox_overflow = float(metrics.get("bbox_overflow_pts", 0.0))
        baseline_drift = float(metrics.get("baseline_drift_pts", 0.0))
        font_distance = float(metrics.get("font_match_distance", 0.05))

        overflow_score = max(0.0, 1.0 - (bbox_overflow / 0.5)) if bbox_overflow <= 0.5 else 0.0
        drift_score = 1.0 if abs(baseline_drift) == 0.0 else max(0.0, 1.0 - abs(baseline_drift))
        font_score = max(0.0, 1.0 - (font_distance / 0.20))

        t2_composite = round(0.40 * overflow_score + 0.35 * drift_score + 0.25 * font_score, 4)
        t2_passed = t2_composite >= 0.85

        # Tier 3 Rendering Quality
        raster_diff_ratio = float(metrics.get("raster_diff_ratio", 0.001))
        t3_score = max(0.0, round(1.0 - (raster_diff_ratio * 100.0), 4))
        t3_passed = t3_score >= 0.80

        overall_score = round((0.40 if t1_passed else 0.0) + 0.40 * t2_composite + 0.20 * t3_score, 4)
        eval_run_id = f"eval_pedit_{doc_id}_{uuid.uuid4().hex[:6]}"
        eval_trace_id = f"trace_pedit_{doc_id}_{uuid.uuid4().hex[:6]}"

        record_run(
            session=self.session,
            run_id=eval_run_id,
            trace_id=eval_trace_id,
            project=self.project,
            task_type=f"pdf_transform_{operation}",
            agent_version=champion.version,
            status="succeeded" if (t1_passed and t2_passed) else "failed",
            input_hash=doc_id,
            output_hash=str(overall_score),
            artifact_uris=[],
            cost_cents=0.0,
            latency_ms=int(metrics.get("latency_ms", 250)),
            failure_code="PDF_CORRUPTION" if not t1_passed else ("LAYOUT_OVERFLOW" if not t2_passed else None),
            error_message="; ".join([c["reason"] for c in t1_checks.values() if not c["passed"]]) if not t1_passed else None,
            user_feedback=trace_payload,
        )

        record_evaluation(
            session=self.session,
            run_id=eval_run_id,
            evaluator_name="zero_corruption_invariants",
            tier=1,
            passed=t1_passed,
            score=1.0 if t1_passed else 0.0,
            rationale="All Tier 1 invariants passed" if t1_passed else "Tier 1 failure",
            metrics=t1_checks,
        )

        record_evaluation(
            session=self.session,
            run_id=eval_run_id,
            evaluator_name="spatial_alignment_and_font_fidelity",
            tier=2,
            passed=t2_passed,
            score=t2_composite,
            rationale=f"Overflow score {overflow_score:.2f}, baseline {drift_score:.2f}, font {font_score:.2f}",
            metrics={
                "bbox_overflow_pts": bbox_overflow,
                "baseline_drift_pts": baseline_drift,
                "font_match_distance": font_distance,
            },
        )

        record_evaluation(
            session=self.session,
            run_id=eval_run_id,
            evaluator_name="visual_raster_diff",
            tier=3,
            passed=t3_passed,
            score=t3_score,
            rationale=f"Raster diff ratio {raster_diff_ratio:.5f}",
            metrics={"raster_diff_ratio": raster_diff_ratio},
        )

        return {
            "doc_id": doc_id,
            "overall_score": overall_score,
            "tier1_passed": t1_passed,
            "tier2_score": t2_composite,
            "tier3_score": t3_score,
            "status": "succeeded" if (t1_passed and t2_passed) else "failed",
        }

