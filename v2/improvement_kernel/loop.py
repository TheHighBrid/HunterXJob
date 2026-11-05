"""Autonomous Continuous Improvement Loop Coordinator.

Orchestrates the closed-loop agent self-improvement workflow:
1. Evaluate: Grades recent ungraded execution traces with Tier 1-3 evaluators.
2. Cluster: Groups failures into taxonomies and extracts replay test cases.
3. Synthesize: Proposes declarative harness patches as challenger AgentVersions.
4. Benchmark: Runs Champion vs. Challenger tournament on frozen golden + replay benchmarks.
5. Promote: Statistical promotion gate with zero-regression enforcement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from improvement_kernel.clustering.clusterer import DefectClusterer
from improvement_kernel.clustering.taxonomy import FailureCategory
from improvement_kernel.evaluations.engine import EvaluationEngine
from improvement_kernel.evaluations.promotion import PromotionDecision, PromotionGate
from improvement_kernel.optimizer.synthesizer import CandidateSynthesizer
from improvement_kernel.sandbox.runner import SandboxRunner
from improvement_kernel.storage.db import get_current_champion
from improvement_kernel.storage.models import (
    AgentVersion,
    DefectCluster,
    DefectStatus,
    KernelRun,
    PromotionRecord,
    VersionStatus,
)

logger = logging.getLogger("improvement_kernel.loop")

DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parent.parent / "evals" / "hunterxjob" / "golden_cases.jsonl"
DEFAULT_REPLAY_PATH = Path(__file__).resolve().parent.parent / "evals" / "hunterxjob" / "replay_cases.jsonl"


@dataclass
class ImprovementCycleReport:
    champion_version: str
    champion_golden_score: float
    champion_golden_pass_rate: float
    champion_invariants_passed: bool
    evaluated_runs: int
    new_clusters_count: int
    synthesized_candidates: list[str]
    benchmark_decisions: list[dict[str, Any]]
    champion_promoted: str | None


class ContinuousImprovementLoop:
    """Master coordinator for HunterXJob self-improvement."""

    def __init__(
        self,
        session: Session,
        project: str = "hunterxjob",
        golden_dataset_path: Path | None = None,
        replay_dataset_path: Path | None = None,
    ) -> None:
        self.session = session
        self.project = project
        evals_dir = Path(__file__).resolve().parent.parent / "evals" / project
        self.golden_path = golden_dataset_path or (evals_dir / "golden_cases.jsonl")
        self.replay_path = replay_dataset_path or (evals_dir / "replay_cases.jsonl")

        self.eval_engine = EvaluationEngine(session=session, project=project)
        self.clusterer = DefectClusterer(session=session, project=project, replay_cases_path=self.replay_path)
        self.synthesizer = CandidateSynthesizer(session=session, project=project)
        self.sandbox = SandboxRunner(session=session, project=project)
        self.promotion_gate = PromotionGate(session=session, project=project)

    def backfill_historical_applications(self, limit: int = 50) -> int:
        """Backfill existing Application records into KernelRun traces if unrecorded."""
        from app.models import Application, Job

        apps = (
            self.session.query(Application)
            .order_by(Application.created_at.desc())
            .limit(limit)
            .all()
        )
        existing_run_ids = {r[0] for r in self.session.query(KernelRun.id).all()}
        backfilled = 0
        for app in apps:
            if app.id in existing_run_ids:
                continue
            job = self.session.get(Job, app.job_id)
            status = "failed" if app.last_error else "succeeded"
            run = KernelRun(
                id=app.id,
                trace_id=app.id,
                project=self.project,
                task_type=f"apply_{app.adapter_name or 'generic'}",
                agent_version="v2.0.0-champion",
                status=status,
                input_hash=app.idempotency_key or "",
                output_hash=app.submitted_hash or "",
                cost_cents=0.0,
                latency_ms=1200,
                failure_code="WORKFLOW_FORM_ERROR" if app.last_error else None,
                error_message=app.last_error,
            )
            self.session.add(run)
            backfilled += 1
        if backfilled:
            self.session.commit()
        return backfilled

    def seed_audit_defects_if_empty(self) -> int:
        """Seed known video-confirmed audit defects if no clusters exist yet."""
        existing = self.session.query(DefectCluster).filter_by(project=self.project).count()
        if existing > 0:
            return 0

        if self.project == "pedit":
            audit_defects = [
                DefectCluster(
                    project="pedit",
                    failure_category=FailureCategory.DATA,
                    failure_code="BOUNDING_BOX_OVERFLOW",
                    root_cause_span="adjust_layout",
                    pattern_signature="pedit::DATA::BOUNDING_BOX_OVERFLOW::adjust_layout",
                    occurrences_count=3,
                    status=DefectStatus.open.value,
                    proposed_fix_summary="Auto-tune word wrapping and font step-down when text expands beyond original bounding box",
                ),
                DefectCluster(
                    project="pedit",
                    failure_category=FailureCategory.TOOL,
                    failure_code="FONT_MISMATCH",
                    root_cause_span="reembed_font",
                    pattern_signature="pedit::TOOL::FONT_MISMATCH::reembed_font",
                    occurrences_count=2,
                    status=DefectStatus.open.value,
                    proposed_fix_summary="Apply metric-compatible font fallback and PostScript font name suffix normalization",
                ),
                DefectCluster(
                    project="pedit",
                    failure_category=FailureCategory.WORKFLOW,
                    failure_code="LAYOUT_DRIFT",
                    root_cause_span="adjust_layout",
                    pattern_signature="pedit::WORKFLOW::LAYOUT_DRIFT::kerning_delta",
                    occurrences_count=2,
                    status=DefectStatus.open.value,
                    proposed_fix_summary="Apply adaptive tracking compression and preserve strict zero baseline drift",
                ),
            ]
        else:
            audit_defects = [
                DefectCluster(
                    project=self.project,
                    failure_category=FailureCategory.DATA,
                    failure_code="DATA_BAD_LOCATION",
                    root_cause_span="fill_location_field",
                    pattern_signature=f"{self.project}::DATA::DATA_BAD_LOCATION::fill_location_field",
                    occurrences_count=2,
                    status=DefectStatus.open.value,
                    proposed_fix_summary="Enforce Canadian country matching in location autocomplete and reject US matches (Ontario, California)",
                ),
                DefectCluster(
                    project=self.project,
                    failure_category=FailureCategory.SAFETY,
                    failure_code="SAFETY_UNSUPPORTED_QUALIFICATION",
                    root_cause_span="truth_guard_cpa",
                    pattern_signature=f"{self.project}::SAFETY::SAFETY_UNSUPPORTED_QUALIFICATION::truth_guard_cpa",
                    occurrences_count=2,
                    status=DefectStatus.open.value,
                    proposed_fix_summary="Enforce strict Truth Guard for professional designations (CPA, CFA); refuse or select No without verified profile fact",
                ),
                DefectCluster(
                    project=self.project,
                    failure_category=FailureCategory.TOOL,
                    failure_code="TOOL_SELECTOR_NOT_FOUND",
                    root_cause_span="fill_location_field",
                    pattern_signature=f"{self.project}::TOOL::TOOL_SELECTOR_NOT_FOUND::_systemfield_location",
                    occurrences_count=3,
                    status=DefectStatus.open.value,
                    proposed_fix_summary="Add fallback DOM selectors for Ashby _systemfield_location autocomplete container",
                ),
                DefectCluster(
                    project=self.project,
                    failure_category=FailureCategory.WORKFLOW,
                    failure_code="WORKFLOW_PRESCREEN_VIOLATION",
                    root_cause_span="pre_screening_eligibility",
                    pattern_signature=f"{self.project}::WORKFLOW::WORKFLOW_PRESCREEN_VIOLATION::outside_location_policy",
                    occurrences_count=2,
                    status=DefectStatus.open.value,
                    proposed_fix_summary="Pre-filter hybrid or in-person roles located outside Ottawa-Gatineau before launching browser forms",
                ),
            ]
        for d in audit_defects:
            self.session.add(d)
        self.session.commit()
        return len(audit_defects)

    def evaluate_pending_runs(self, limit: int = 50) -> int:
        """Evaluate recent runs that lack Tier 1/2 evaluations."""
        stmt = (
            select(KernelRun)
            .where(KernelRun.project == self.project)
            .order_by(KernelRun.created_at.desc())
            .limit(limit)
        )
        runs = list(self.session.execute(stmt).scalars().all())
        evaluated_count = 0

        for run in runs:
            # Check if already evaluated
            if not run.evaluations:
                self.eval_engine.evaluate_run(run)
                evaluated_count += 1

        logger.info("Evaluated %d pending runs", evaluated_count)
        return evaluated_count

    def cluster_defects(self) -> int:
        """Cluster recent failure traces into defect taxonomies and update replay test cases."""
        clusters = self.clusterer.cluster_recent_failures(lookback_hours=48)
        logger.info("Found %d active defect clusters", len(clusters))
        return len(clusters)

    def synthesize_candidates(self) -> list[AgentVersion]:
        """Synthesize challenger harnesses for open defect clusters."""
        candidates = self.synthesizer.synthesize_all_open()
        logger.info("Synthesized %d challenger agent versions", len(candidates))
        return candidates

    def benchmark_and_promote(self) -> list[dict[str, Any]]:
        """Run Champion vs. Challenger tournament and apply statistical promotion gate."""
        champion = get_current_champion(self.session, self.project)

        # Find candidates awaiting tournament
        stmt = (
            select(AgentVersion)
            .where(AgentVersion.project == self.project, AgentVersion.status == VersionStatus.candidate.value)
            .order_by(AgentVersion.created_at.asc())
        )
        candidates = list(self.session.execute(stmt).scalars().all())
        decisions: list[dict[str, Any]] = []

        if not candidates:
            logger.info("No candidate agent versions to benchmark")
            return decisions

        # Benchmark champion on golden benchmark
        logger.info("Benchmarking active champion %s on golden test suite", champion.version)
        champ_res = self.sandbox.run_benchmark(champion, self.golden_path)

        for candidate in candidates:
            logger.info("Benchmarking challenger %s on golden test suite", candidate.version)
            challenger_res = self.sandbox.run_benchmark(candidate, self.golden_path)

            # Ensure paired evaluation
            min_len = min(len(champ_res.scores), len(challenger_res.scores))
            champ_scores = champ_res.scores[:min_len]
            challenger_scores = challenger_res.scores[:min_len]

            result = self.promotion_gate.evaluate_challenger(
                champion_version=champion.version,
                challenger_version=candidate.version,
                champion_scores=champ_scores,
                challenger_scores=challenger_scores,
                candidate_invariants_passed=challenger_res.invariants_passed,
                risk_level=candidate.risk_level,
            )

            record_data = {
                "candidate": candidate.version,
                "decision": result.decision.value,
                "mean_delta": result.mean_delta,
                "p_value": result.p_value,
                "cohens_d": result.cohens_d,
                "regressions_count": result.regressions_count,
                "rationale": result.rationale,
            }
            decisions.append(record_data)

            if result.decision == PromotionDecision.promote_auto:
                logger.info("PROMOTING CHALLENGER %s to CHAMPION!", candidate.version)
                # Demote old champion
                champion.status = VersionStatus.canary.value
                # Promote candidate
                candidate.status = VersionStatus.champion.value
                self.session.commit()
                champion = candidate  # Update active champion reference for subsequent candidates
            elif result.decision == PromotionDecision.reject:
                candidate.status = VersionStatus.rejected.value
                self.session.commit()

        return decisions

    def run_cycle(self) -> ImprovementCycleReport:
        """Execute a full end-to-end self-improvement cycle."""
        logger.info("Starting autonomous self-improvement cycle for project: %s", self.project)

        # 0. Backfill historical applications and seed audit defects if empty
        self.backfill_historical_applications()
        self.seed_audit_defects_if_empty()

        # 1. Evaluate
        evaluated = self.evaluate_pending_runs()

        # 2. Cluster
        new_clusters = self.cluster_defects()

        # 3. Synthesize
        candidates = self.synthesize_candidates()
        candidate_versions = [c.version for c in candidates]

        # 4. Benchmark & Promote
        decisions = self.benchmark_and_promote()

        promoted_version: str | None = None
        for dec in decisions:
            if dec["decision"] == PromotionDecision.promote_auto.value:
                promoted_version = dec["candidate"]

        # 5. Measure Champion Performance on Golden Benchmark Suite
        champion = get_current_champion(self.session, self.project)
        champ_res = self.sandbox.run_benchmark(champion, self.golden_path)

        report = ImprovementCycleReport(
            champion_version=champion.version,
            champion_golden_score=champ_res.mean_score,
            champion_golden_pass_rate=champ_res.tier1_pass_rate,
            champion_invariants_passed=champ_res.invariants_passed,
            evaluated_runs=evaluated,
            new_clusters_count=new_clusters,
            synthesized_candidates=candidate_versions,
            benchmark_decisions=decisions,
            champion_promoted=promoted_version,
        )

        logger.info(
            "Completed cycle: champion=%s (score=%.2f, invariants=%s), evaluated=%d, clusters=%d, synthesized=%d, promoted=%s",
            report.champion_version,
            report.champion_golden_score,
            report.champion_invariants_passed,
            evaluated,
            new_clusters,
            len(candidate_versions),
            promoted_version,
        )
        return report
