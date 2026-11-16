"""Storage models for the Autonomous Improvement Kernel.

Persists:
- agent_versions: Versioned harnesses (champion, candidate, staging, rolled_back)
- kernel_runs: Execution runs with inputs/outputs hashes, costs, latencies, failure codes
- kernel_traces: Hierarchical OpenTelemetry GenAI spans (invoke_agent, chat, execute_tool)
- kernel_evaluations: 3-tier grading records (Tier 1 deterministic, Tier 2 domain, Tier 3 judge)
- kernel_defects: Clustered failure modes with causal root-cause signatures
- kernel_improvement_jobs: Zero-broker transactional job queue for asynchronous grading & optimization
- kernel_promotions: Champion vs Challenger tournament decision records
"""
from __future__ import annotations

import enum
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def gen_uuid() -> str:
    return str(uuid.uuid4())


class VersionStatus(str, enum.Enum):
    candidate = "candidate"
    staging = "staging"
    canary = "canary"
    champion = "champion"
    rejected = "rejected"
    rolled_back = "rolled_back"


class RunStatus(str, enum.Enum):
    succeeded = "succeeded"
    failed = "failed"
    timeout = "timeout"
    guardrail_blocked = "guardrail_blocked"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    dead = "dead"


class DefectStatus(str, enum.Enum):
    open = "open"
    investigating = "investigating"
    synthesized = "synthesized"
    resolved = "resolved"


class AgentVersion(Base):
    """Versioned agent harness representing system instructions, selectors, policies, and configs."""

    __tablename__ = "agent_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    project: Mapped[str] = mapped_column(String(64), nullable=False, default="hunterxjob")
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    code_diff_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    harness_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=VersionStatus.candidate.value)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project", "version", name="uq_agent_versions_project_version"),
        Index("idx_agent_versions_status", "project", "status"),
    )

    @property
    def harness_config(self) -> dict[str, Any]:
        try:
            return json.loads(self.harness_config_json)
        except Exception:
            return {}

    @harness_config.setter
    def harness_config(self, value: dict[str, Any]) -> None:
        self.harness_config_json = json.dumps(value, sort_keys=True)


class KernelRun(Base):
    """A single execution attempt of an agentic workflow."""

    __tablename__ = "kernel_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project: Mapped[str] = mapped_column(String(64), nullable=False, default="hunterxjob", index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, default="application")
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1.0.0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.succeeded.value)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_uris_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    cost_cents: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_feedback_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)

    traces: Mapped[list[TraceSpan]] = relationship("TraceSpan", back_populates="run", cascade="all, delete-orphan")
    evaluations: Mapped[list[KernelEvaluation]] = relationship("KernelEvaluation", back_populates="run", cascade="all, delete-orphan")


class TraceSpan(Base):
    """Hierarchical OpenTelemetry-compatible GenAI telemetry span."""

    __tablename__ = "kernel_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("kernel_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    span_type: Mapped[str] = mapped_column(String(64), nullable=False, default="invoke_agent")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    attributes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    run: Mapped[KernelRun] = relationship("KernelRun", back_populates="traces")


class KernelEvaluation(Base):
    """Structured evaluation score across Tier 1, Tier 2, or Tier 3."""

    __tablename__ = "kernel_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("kernel_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    evaluator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = Invariant, 2 = Domain, 3 = LLM Judge
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    run: Mapped[KernelRun] = relationship("KernelRun", back_populates="evaluations")


class DefectCluster(Base):
    """Recurring failure pattern identified by causal span analysis and clustering."""

    __tablename__ = "kernel_defects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    project: Mapped[str] = mapped_column(String(64), nullable=False, default="hunterxjob", index=True)
    failure_category: Mapped[str] = mapped_column(String(32), nullable=False)  # MODEL, TOOL, WORKFLOW, DATA, PRODUCT, SAFETY
    failure_code: Mapped[str] = mapped_column(String(64), nullable=False)
    root_cause_span: Mapped[str] = mapped_column(String(128), nullable=False)
    pattern_signature: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    occurrences_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    example_run_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DefectStatus.open.value)
    proposed_fix_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ImprovementJob(Base):
    """Asynchronous background work item for grading, clustering, or candidate synthesis."""

    __tablename__ = "kernel_improvement_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)  # grade_run, cluster_defects, synthesize_candidate, run_benchmark
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JobStatus.pending.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PromotionRecord(Base):
    """Cryptographic record of a Champion vs. Challenger promotion decision."""

    __tablename__ = "kernel_promotions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    project: Mapped[str] = mapped_column(String(64), nullable=False, default="hunterxjob", index=True)
    champion_version: Mapped[str] = mapped_column(String(64), nullable=False)
    challenger_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # promote_auto, human_review_required, reject, rolled_back
    mean_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p_value: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    cohens_d: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    regressions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invariants_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    promoted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
