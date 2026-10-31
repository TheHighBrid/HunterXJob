"""Evaluation Engine coordinating Tier 1, Tier 2, and Tier 3 grading."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from improvement_kernel.evaluations.tier1 import evaluate_tier1_invariants
from improvement_kernel.evaluations.tier2 import evaluate_tier2_metrics
from improvement_kernel.evaluations.tier3 import evaluate_tier3_judge
from improvement_kernel.storage.db import enqueue_job, record_evaluation
from improvement_kernel.storage.models import KernelRun, TraceSpan


@dataclass
class RunGradingResult:
    run_id: str
    passed: bool
    overall_score: float
    tier1_passed: bool
    tier2_score: float
    tier3_score: float
    failure_code: str | None
    rationale: str


def grade_run(session: Session, run_id: str) -> RunGradingResult:
    """Execute the full 3-tier evaluation pipeline on a completed run."""
    run = session.execute(select(KernelRun).where(KernelRun.id == run_id)).scalar_one_or_none()
    if not run:
        raise ValueError(f"Run {run_id} not found in database")

    spans = session.execute(
        select(TraceSpan).where(TraceSpan.run_id == run_id).order_by(TraceSpan.created_at.asc())
    ).scalars().all()

    # Project-specific domain dispatch: Fidelis image restoration
    if run.project == "fidelis":
        from improvement_kernel.adapters.fidelis import FidelisAdapter
        adapter = FidelisAdapter(session=session, project="fidelis")
        payload = {
            "image_id": run.input_hash or run.id,
            "elapsed_ms": run.latency_ms or 1500,
            "input_sha256": run.input_hash,
            "output_sha256": run.output_hash,
        }
        for s in spans:
            if s.attributes_json:
                try:
                    attrs = json.loads(s.attributes_json)
                    payload.update(attrs)
                except Exception:
                    pass
        res = adapter.evaluate_restoration_trace(payload)
        return RunGradingResult(
            run_id=run_id,
            passed=res["passed"],
            overall_score=res["overall_score"],
            tier1_passed=res["tier1"]["passed"],
            tier2_score=res["tier2"]["composite_score"],
            tier3_score=res["tier3"]["score"],
            failure_code=res.get("failure_code"),
            rationale=res["rationale"],
        )

    # 1. Tier 1 Deterministic Invariants (Hard Circuit Breaker)
    t1_passed, t1_checks = evaluate_tier1_invariants(run, spans)
    t1_rationale = "; ".join(c.rationale for c in t1_checks if not c.passed) or "All deterministic invariants passed."
    failed_check = next((c for c in t1_checks if not c.passed), None)

    record_evaluation(
        session=session,
        run_id=run_id,
        evaluator_name="tier1_invariants",
        tier=1,
        passed=t1_passed,
        score=1.0 if t1_passed else 0.0,
        rationale=t1_rationale,
        metrics={"checks_total": len(t1_checks), "checks_passed": sum(1 for c in t1_checks if c.passed)},
    )

    if not t1_passed:
        # Hard fail-closed stop: no tokens or grading spent on Tier 2/3
        failure_code = failed_check.failure_code if failed_check else "TIER1_INVARIANT_FAILED"
        run.failure_code = failure_code
        run.status = "failed"
        session.commit()

        # Enqueue failure clustering
        enqueue_job(session, "cluster_defects", {"run_id": run_id, "failure_code": failure_code, "project": run.project})

        return RunGradingResult(
            run_id=run_id,
            passed=False,
            overall_score=0.0,
            tier1_passed=False,
            tier2_score=0.0,
            tier3_score=0.0,
            failure_code=failure_code,
            rationale=f"Hard circuit breaker tripped: {t1_rationale}",
        )

    # 2. Tier 2 Domain Metrics
    t2_res = evaluate_tier2_metrics(run, spans)
    record_evaluation(
        session=session,
        run_id=run_id,
        evaluator_name="tier2_domain_metrics",
        tier=2,
        passed=t2_res.passed,
        score=t2_res.score,
        rationale=t2_res.rationale,
        metrics=t2_res.metrics,
    )

    # 3. Tier 3 Calibrated Rubric Judge
    t3_res = evaluate_tier3_judge(run, spans)
    record_evaluation(
        session=session,
        run_id=run_id,
        evaluator_name="tier3_rubric_judge",
        tier=3,
        passed=t3_res.passed,
        score=t3_res.score,
        rationale=t3_res.rationale,
        metrics=t3_res.scores,
    )

    # Composite Score (40% domain metrics + 60% rubric judge)
    overall_score = round(0.40 * t2_res.score + 0.60 * t3_res.score, 4)
    run_passed = t1_passed and t2_res.passed and t3_res.passed and overall_score >= 0.75

    failure_code = None
    if not run_passed:
        failure_code = run.failure_code or "EVAL_SCORE_BELOW_THRESHOLD"
        run.failure_code = failure_code
        run.status = "failed"
        session.commit()
        enqueue_job(session, "cluster_defects", {"run_id": run_id, "failure_code": failure_code, "project": run.project})

    return RunGradingResult(
        run_id=run_id,
        passed=run_passed,
        overall_score=overall_score,
        tier1_passed=True,
        tier2_score=t2_res.score,
        tier3_score=t3_res.score,
        failure_code=failure_code,
        rationale=f"T1=Pass, T2={t2_res.score:.2f}, T3={t3_res.score:.2f} -> Overall={overall_score:.2f}",
    )


class EvaluationEngine:
    """Service providing 3-tier grading on execution runs."""

    def __init__(self, session: Session, project: str = "hunterxjob") -> None:
        self.session = session
        self.project = project

    def evaluate_run(self, run: KernelRun) -> RunGradingResult:
        """Grade a kernel run across Tier 1, 2, and 3."""
        return grade_run(self.session, run.id)
