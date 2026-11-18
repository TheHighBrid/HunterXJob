"""Agent Gang Multi-Agent Telemetry and Handoff Evaluation Adapter.

Connects Agent Gang orchestrator routing, tool selection, and specialist delegation
to the Autonomous Improvement Kernel.
Evaluates:
- Tool choice accuracy and schema validation
- Unnecessary handoff penalty (minimizing roundtrips between agents)
- Task completion rate without human intervention
- Token/cost efficiency and latency budgets
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from improvement_kernel.runtime.runner import ImprovementRuntime, RunContext
from improvement_kernel.storage.db import get_current_champion

logger = logging.getLogger("improvement_kernel.adapters.agent_gang")


class AgentGangAdapter:
    """Connects Agent Gang multi-agent routing to the improvement runtime."""

    def __init__(self, session: Session, project: str = "agent_gang") -> None:
        self.session = session
        self.project = project
        self.runtime = ImprovementRuntime(session=session, project=project)

    def start_task_run(
        self,
        task_id: str,
        user_prompt: str,
        orchestrator_model: str = "llama3.2:3b",
        extra_inputs: dict[str, Any] | None = None,
    ) -> RunContext:
        """Start a traced multi-agent task execution."""
        champion = get_current_champion(self.session, self.project)
        inputs = {
            "task_id": task_id,
            "user_prompt": user_prompt,
            "orchestrator_model": orchestrator_model,
        }
        if extra_inputs:
            inputs.update(extra_inputs)

        return self.runtime.start_run(
            task_type="orchestrator_execution",
            agent_version=champion.version,
            inputs=inputs,
        )

    def trace_tool_selection(
        self,
        ctx: RunContext,
        agent_role: str,
        tool_name: str,
        arguments_valid: bool,
        is_optimal_choice: bool,
    ) -> None:
        """Record tool selection span."""
        with ctx.span("select_tool", f"{agent_role}_{tool_name}") as span:
            span.set_attributes({
                "agent_role": agent_role,
                "tool_name": tool_name,
                "arguments_valid": arguments_valid,
                "is_optimal_choice": is_optimal_choice,
            })
            if not arguments_valid:
                span.record_error("SCHEMA_VALIDATION_ERROR", f"Invalid schema arguments passed to {tool_name}")
            if not is_optimal_choice:
                span.record_error("SUBOPTIMAL_TOOL", f"Selected {tool_name} when a more direct tool was available")

    def trace_agent_handoff(
        self,
        ctx: RunContext,
        from_agent: str,
        to_agent: str,
        handoff_reason: str,
        was_necessary: bool,
    ) -> None:
        """Record inter-agent handoff span to penalize excessive swarm bouncing."""
        with ctx.span("agent_handoff", f"{from_agent}_to_{to_agent}") as span:
            span.set_attributes({
                "from_agent": from_agent,
                "to_agent": to_agent,
                "handoff_reason": handoff_reason,
                "was_necessary": was_necessary,
            })
            if not was_necessary:
                span.record_error("UNNECESSARY_HANDOFF", f"Superfluous delegation from {from_agent} to {to_agent}")

    def evaluate_swarm_trace(self, trace_payload: dict[str, Any]) -> dict[str, Any]:
        """Evaluate a multi-agent swarm task execution trace against 3-tier invariants."""
        import uuid
        from improvement_kernel.storage.db import record_evaluation, record_run

        task_id = str(trace_payload.get("task_id") or f"task_{uuid.uuid4().hex[:8]}")
        metrics = dict(trace_payload.get("metrics", {}))
        champion = get_current_champion(self.session, self.project)

        # Tier 1 Invariants
        has_deadlock = bool(metrics.get("has_deadlock", False))
        unauthorized_tool = bool(metrics.get("unauthorized_tool_executed", False))
        token_ceiling_exceeded = bool(metrics.get("token_ceiling_exceeded", False))

        t1_checks = {
            "no_cyclic_deadlock": {
                "passed": not has_deadlock,
                "reason": "Execution completed without cyclic delegation" if not has_deadlock else "Detected infinite loop / delegation deadlock",
            },
            "authorized_tools_only": {
                "passed": not unauthorized_tool,
                "reason": "All executed tools were properly authorized" if not unauthorized_tool else "Executed tool outside agent permissions",
            },
            "token_budget_respected": {
                "passed": not token_ceiling_exceeded,
                "reason": "Token budget within safety ceiling" if not token_ceiling_exceeded else "Exceeded max task token ceiling",
            },
        }
        t1_passed = all(c["passed"] for c in t1_checks.values())

        # Tier 2 Efficiency Metrics
        handoffs = int(metrics.get("total_handoffs", 1))
        unnecessary_handoffs = int(metrics.get("unnecessary_handoffs", 0))
        schema_validation_rate = float(metrics.get("schema_validation_rate", 1.0))
        handoff_efficiency = max(0.0, 1.0 - (unnecessary_handoffs / max(1, handoffs)))
        t2_composite = round(0.50 * handoff_efficiency + 0.50 * schema_validation_rate, 4)
        t2_passed = t2_composite >= 0.80

        # Tier 3 Consensus Quality
        consensus_score = float(metrics.get("consensus_score", 0.90))
        t3_score = round(consensus_score, 4)
        t3_passed = t3_score >= 0.75

        overall_score = round((0.40 if t1_passed else 0.0) + 0.40 * t2_composite + 0.20 * t3_score, 4)
        eval_run_id = f"eval_gang_{task_id}_{uuid.uuid4().hex[:6]}"

        record_run(
            session=self.session,
            run_id=eval_run_id,
            project=self.project,
            task_type="swarm_orchestration",
            agent_version=champion.version,
            status="succeeded" if (t1_passed and t2_passed) else "failed",
            input_hash=task_id,
            output_hash=str(overall_score),
            artifact_uris=[],
            cost_cents=float(metrics.get("cost_cents", 0.0)),
            latency_ms=int(metrics.get("latency_ms", 1200)),
            failure_code="DEADLOCK_CYCLE" if has_deadlock else ("UNNECESSARY_HANDOFF" if not t2_passed else None),
            error_message="; ".join([c["reason"] for c in t1_checks.values() if not c["passed"]]) if not t1_passed else None,
            user_feedback=trace_payload,
        )

        record_evaluation(
            session=self.session,
            run_id=eval_run_id,
            evaluator_name="safety_and_deadlock_invariants",
            tier=1,
            passed=t1_passed,
            score=1.0 if t1_passed else 0.0,
            rationale="All safety invariants passed" if t1_passed else "Invariant breach",
            metrics=t1_checks,
        )

        record_evaluation(
            session=self.session,
            run_id=eval_run_id,
            evaluator_name="swarm_efficiency",
            tier=2,
            passed=t2_passed,
            score=t2_composite,
            rationale=f"Handoff efficiency {handoff_efficiency:.2f}, schema validation {schema_validation_rate:.2f}",
            metrics={
                "total_handoffs": handoffs,
                "unnecessary_handoffs": unnecessary_handoffs,
                "schema_validation_rate": schema_validation_rate,
            },
        )

        record_evaluation(
            session=self.session,
            run_id=eval_run_id,
            evaluator_name="synthesis_and_consensus",
            tier=3,
            passed=t3_passed,
            score=t3_score,
            rationale=f"Consensus score {consensus_score:.2f}",
            metrics={"consensus_score": consensus_score},
        )

        return {
            "task_id": task_id,
            "overall_score": overall_score,
            "tier1_passed": t1_passed,
            "tier2_score": t2_composite,
            "tier3_score": t3_score,
            "status": "succeeded" if (t1_passed and t2_passed) else "failed",
        }

