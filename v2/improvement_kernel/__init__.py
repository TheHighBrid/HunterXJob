"""Autonomous Agent Improvement Kernel for HunterXJob and Multi-Project Ecosystem.

Closed-loop agent self-improvement architecture:
- Hierarchical OpenTelemetry GenAI runtime tracing & Presidio PII redaction
- 3-Tier Evaluation Engine: Deterministic Invariants, Domain Metrics & Calibrated JSON Judge
- Failure Clustering & Causal Root-Cause Analysis (MODEL, TOOL, WORKFLOW, DATA, PRODUCT, SAFETY)
- Declarative Harness Evolution (selectors, answer policies, prompt templates, decision weights)
- Sandbox Benchmarking against Frozen Golden & Failure Replay Datasets
- Statistical Champion-vs-Challenger Promotion Gate (Wilcoxon Signed-Rank + Cohen's d + Zero Regressions)
- Atomic Rollback & Fail-Closed Human Review Governance
"""
from __future__ import annotations

__version__ = "1.0.0"

from improvement_kernel.adapters.hunterxjob import HunterXJobAdapter
from improvement_kernel.clustering.clusterer import DefectClusterer
from improvement_kernel.clustering.taxonomy import FailureCategory
from improvement_kernel.evaluations.engine import EvaluationEngine
from improvement_kernel.evaluations.promotion import PromotionDecision, PromotionGate
from improvement_kernel.loop import ContinuousImprovementLoop, ImprovementCycleReport
from improvement_kernel.optimizer.harness import AgentHarness, default_harness
from improvement_kernel.optimizer.synthesizer import CandidateSynthesizer
from improvement_kernel.runtime.redactor import TraceRedactor
from improvement_kernel.runtime.runner import ImprovementRuntime, RunContext, improvement_runtime
from improvement_kernel.sandbox.runner import SandboxBenchmarkResult, SandboxRunner
from improvement_kernel.storage.db import get_current_champion, init_kernel_db
from improvement_kernel.storage.models import (
    AgentVersion,
    DefectCluster,
    ImprovementJob,
    KernelEvaluation,
    KernelRun,
    PromotionRecord,
    TraceSpan,
    VersionStatus,
)

__all__ = [
    "__version__",
    "AgentHarness",
    "default_harness",
    "AgentVersion",
    "DefectCluster",
    "ImprovementJob",
    "KernelEvaluation",
    "KernelRun",
    "PromotionRecord",
    "TraceSpan",
    "VersionStatus",
    "FailureCategory",
    "HunterXJobAdapter",
    "DefectClusterer",
    "EvaluationEngine",
    "PromotionDecision",
    "PromotionGate",
    "ContinuousImprovementLoop",
    "ImprovementCycleReport",
    "CandidateSynthesizer",
    "ImprovementRuntime",
    "RunContext",
    "improvement_runtime",
    "TraceRedactor",
    "SandboxRunner",
    "SandboxBenchmarkResult",
    "get_current_champion",
    "init_kernel_db",
]
