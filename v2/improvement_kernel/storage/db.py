"""Database helper functions for the Autonomous Improvement Kernel."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import Base, engine as default_engine
from improvement_kernel.storage.models import (
    AgentVersion,
    DefectCluster,
    ImprovementJob,
    JobStatus,
    KernelEvaluation,
    KernelRun,
    PromotionRecord,
    RunStatus,
    TraceSpan,
    VersionStatus,
    utcnow,
)

DEFAULT_CHAMPION_VERSION = "v2.0.0-champion"


def init_kernel_db(engine=None) -> None:
    """Ensure kernel tables exist in the database."""
    eng = engine or default_engine
    Base.metadata.create_all(bind=eng)


def get_current_champion(session: Session, project: str = "hunterxjob") -> AgentVersion:
    """Get the active champion agent version for a project, creating a default baseline if none exists."""
    stmt = (
        select(AgentVersion)
        .where(AgentVersion.project == project, AgentVersion.status == VersionStatus.champion.value)
        .order_by(AgentVersion.created_at.desc())
    )
    champion = session.execute(stmt).scalar_one_or_none()
    if champion:
        return champion

    # Create default baseline champion
    default_harness = {
        "selectors": {
            "name": ["input[name='name']", "#name", "input[data-qa*='name']", "input[data-ui*='name']"],
            "email": ["input[type='email']", "#email", "input[name='email']"],
            "phone": ["input[type='tel']", "#phone", "input[name='phone']"],
            "resume": ["input[type='file'][id='resume']", "input[type='file'][name='resume']", "input[type='file']"],
            "location": ["input[name='location']", "#location", "[data-qa*='location']", "input.location-input"],
        },
        "answer_policies": {
            "work_authorization": "Authorized to work in Canada",
            "relocation": "Yes",
            "bilingual": "Bilingual / Bilingue (English & French / Anglais et Français)",
        },
        "prompt_templates": {
            "question_answering": "Answer the screening question accurately using only verified candidate facts. Never assert unverified certifications.",
        },
        "decision_weights": {
            "min_score_threshold": 65.0,
            "max_commute_km": 40.0,
        },
    }

    champion = AgentVersion(
        project=project,
        version=DEFAULT_CHAMPION_VERSION,
        git_commit="head",
        prompt_hash="default_prompt_v2",
        config_hash="default_config_v2",
        harness_config_json=json.dumps(default_harness, sort_keys=True),
        status=VersionStatus.champion.value,
        risk_level="low",
        notes="Initial baseline champion harness",
    )
    session.add(champion)
    session.commit()
    return champion


def record_run(
    session: Session,
    run_id: str,
    trace_id: str,
    project: str,
    task_type: str,
    agent_version: str,
    status: str,
    input_hash: str,
    output_hash: str | None,
    artifact_uris: list[str],
    cost_cents: float,
    latency_ms: int,
    failure_code: str | None = None,
    error_message: str | None = None,
    user_feedback: dict[str, Any] | None = None,
) -> KernelRun:
    """Persist a task execution run record."""
    run = KernelRun(
        id=run_id,
        trace_id=trace_id,
        project=project,
        task_type=task_type,
        agent_version=agent_version,
        status=status,
        input_hash=input_hash,
        output_hash=output_hash,
        artifact_uris_json=json.dumps(artifact_uris),
        cost_cents=cost_cents,
        latency_ms=latency_ms,
        failure_code=failure_code,
        error_message=error_message,
        user_feedback_json=json.dumps(user_feedback) if user_feedback else None,
    )
    session.add(run)
    session.commit()
    return run


def record_trace_spans(session: Session, spans: list[dict[str, Any]]) -> None:
    """Batch-insert trace spans for a run."""
    for s in spans:
        span = TraceSpan(
            id=s.get("id"),
            run_id=s.get("run_id"),
            parent_id=s.get("parent_id"),
            span_type=s.get("span_type", "execute_tool"),
            name=s.get("name", "tool"),
            attributes_json=json.dumps(s.get("attributes", {})),
            input_json=json.dumps(s.get("input")) if s.get("input") is not None else None,
            output_json=json.dumps(s.get("output")) if s.get("output") is not None else None,
            error_type=s.get("error_type"),
            error_message=s.get("error_message"),
            latency_ms=int(s.get("latency_ms", 0)),
        )
        session.add(span)
    session.commit()


def enqueue_job(
    session: Session,
    job_type: str,
    payload: dict[str, Any],
    max_attempts: int = 5,
) -> ImprovementJob:
    """Enqueue an asynchronous improvement job (grading, clustering, synthesis, benchmark)."""
    job = ImprovementJob(
        job_type=job_type,
        payload_json=json.dumps(payload),
        status=JobStatus.pending.value,
        attempts=0,
        max_attempts=max_attempts,
        next_run_at=utcnow(),
    )
    session.add(job)
    session.commit()
    return job


def record_evaluation(
    session: Session,
    run_id: str,
    evaluator_name: str,
    tier: int,
    passed: bool,
    score: float,
    rationale: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> KernelEvaluation:
    """Record an evaluation result for a run."""
    evaluation = KernelEvaluation(
        run_id=run_id,
        evaluator_name=evaluator_name,
        tier=tier,
        passed=passed,
        score=score,
        rationale=rationale,
        metrics_json=json.dumps(metrics or {}),
    )
    session.add(evaluation)
    session.commit()
    return evaluation
