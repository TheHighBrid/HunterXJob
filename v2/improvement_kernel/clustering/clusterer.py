"""Defect Clustering and Replay Case Generation.

Groups recurring defects into DefectCluster records and converts confirmed
failures into immutable replay test cases in evals/hunterxjob/replay_cases.jsonl.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from improvement_kernel.clustering.analyzer import analyze_causal_root_cause
from improvement_kernel.storage.db import enqueue_job
from improvement_kernel.storage.models import DefectCluster, DefectStatus, KernelRun, RunStatus, TraceSpan

REPLAY_CASES_PATH = Path(__file__).resolve().parents[2] / "evals" / "hunterxjob" / "replay_cases.jsonl"


def cluster_run_failure(
    session: Session,
    run_id: str,
    replay_path: Path | None = None,
) -> DefectCluster:
    """Analyze a failed run, group into a defect cluster, and generate a replay test case."""
    run = session.execute(select(KernelRun).where(KernelRun.id == run_id)).scalar_one_or_none()
    if not run:
        raise ValueError(f"Run {run_id} not found")

    spans = session.execute(
        select(TraceSpan).where(TraceSpan.run_id == run_id).order_by(TraceSpan.created_at.asc())
    ).scalars().all()

    diagnosis = analyze_causal_root_cause(run, spans)

    # Check if defect cluster with this signature already exists
    cluster = session.execute(
        select(DefectCluster).where(DefectCluster.pattern_signature == diagnosis.pattern_signature)
    ).scalar_one_or_none()

    if cluster:
        cluster.occurrences_count += 1
        run_ids = []
        try:
            run_ids = json.loads(cluster.example_run_ids_json)
        except Exception:
            pass
        if run_id not in run_ids:
            run_ids.append(run_id)
        cluster.example_run_ids_json = json.dumps(run_ids[-10:])
        session.commit()
    else:
        cluster = DefectCluster(
            project=run.project,
            failure_category=diagnosis.failure_category,
            failure_code=diagnosis.failure_code,
            root_cause_span=diagnosis.root_cause_span_name,
            pattern_signature=diagnosis.pattern_signature,
            occurrences_count=1,
            example_run_ids_json=json.dumps([run_id]),
            status=DefectStatus.open.value,
            proposed_fix_summary=f"Optimize {diagnosis.diagnostic_target} to remediate {diagnosis.failure_code}",
        )
        session.add(cluster)
        session.commit()

    # Automatically generate a Replay Test Case in evals/hunterxjob/replay_cases.jsonl
    _record_replay_case(run, diagnosis, replay_path or REPLAY_CASES_PATH)

    # Enqueue candidate synthesis job for this defect cluster
    enqueue_job(
        session=session,
        job_type="synthesize_candidate",
        payload={
            "cluster_id": cluster.id,
            "project": run.project,
            "failure_code": diagnosis.failure_code,
            "diagnostic_target": diagnosis.diagnostic_target,
        },
    )

    return cluster


def _record_replay_case(run: Any, diagnosis: Any, target_path: Path) -> None:
    """Append a regression replay case to target_path."""
    target_path.parent.mkdir(parents=True, exist_ok=True)

    replay_entry = {
        "case_id": f"replay_{run.id[:8]}",
        "timestamp": str(getattr(run, "created_at", "")),
        "project": run.project,
        "task_type": run.task_type,
        "failure_code": diagnosis.failure_code,
        "pattern_signature": diagnosis.pattern_signature,
        "root_cause_span": diagnosis.root_cause_span_name,
        "error_message": diagnosis.error_message,
        "input_hash": run.input_hash,
    }

    # Avoid duplicate lines
    existing_cases = []
    if target_path.exists():
        for line in target_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    existing_cases.append(json.loads(line))
                except Exception:
                    pass

    # Don't duplicate signature if already in replay cases
    if not any(c.get("pattern_signature") == diagnosis.pattern_signature for c in existing_cases):
        with open(target_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(replay_entry) + "\n")


class DefectClusterer:
    """Service class for clustering failure runs and managing defect taxonomies."""

    def __init__(
        self,
        session: Session,
        project: str = "hunterxjob",
        replay_cases_path: Path | None = None,
    ) -> None:
        self.session = session
        self.project = project
        self.replay_path = replay_cases_path or REPLAY_CASES_PATH

    def cluster_run(self, run_id: str) -> DefectCluster:
        """Cluster a specific run failure."""
        return cluster_run_failure(self.session, run_id, self.replay_path)

    def cluster_recent_failures(self, lookback_hours: int = 48) -> list[DefectCluster]:
        """Find and cluster recent failed or guardrail_blocked runs."""
        cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
        stmt = (
            select(KernelRun)
            .where(
                KernelRun.project == self.project,
                KernelRun.status.in_([RunStatus.failed.value, RunStatus.guardrail_blocked.value]),
                KernelRun.created_at >= cutoff,
            )
            .order_by(KernelRun.created_at.desc())
        )
        failed_runs = list(self.session.execute(stmt).scalars().all())
        clusters: list[DefectCluster] = []

        for run in failed_runs:
            cluster = self.cluster_run(run.id)
            if cluster not in clusters:
                clusters.append(cluster)

        return clusters
