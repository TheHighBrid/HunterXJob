"""Runtime telemetry engine for hierarchical OpenTelemetry GenAI spans.

Constructs execution trees conforming to OpenTelemetry GenAI Semantic Conventions:
- Root span: invoke_agent {gen_ai.agent.name}
- Child spans: execute_tool {gen_ai.tool.name}, chat {gen_ai.request.model}, verify_guardrail {name}
- Automatic Presidio/Regex redaction before database persistence
- Automatic enqueueing of grading & improvement jobs on completion
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generator

from sqlalchemy.orm import Session

from app.db import engine as default_engine
from improvement_kernel.runtime.redactor import TraceRedactor
from improvement_kernel.storage.db import (
    enqueue_job,
    get_current_champion,
    record_run,
    record_trace_spans,
)
from improvement_kernel.storage.models import RunStatus


@dataclass
class SpanRecord:
    id: str
    run_id: str
    parent_id: str | None
    span_type: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    input: Any = None
    output: Any = None
    error_type: str | None = None
    error_message: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    latency_ms: int = 0

    def finish(self, error: Exception | str | None = None) -> None:
        self.end_time = time.time()
        self.latency_ms = int((self.end_time - self.start_time) * 1000)
        if error is not None:
            if isinstance(error, Exception):
                self.error_type = type(error).__name__
                self.error_message = str(error)
            else:
                self.error_type = "SpanError"
                self.error_message = str(error)

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        self.attributes.update(attrs)

    def record_error(self, err_type: str, err_message: str) -> None:
        self.error_type = err_type
        self.error_message = err_message


class RunContext:
    """Manages active run spans, hierarchical nesting, and telemetry aggregation."""

    def __init__(
        self,
        run_id: str,
        trace_id: str,
        project: str,
        task_type: str,
        agent_version: str,
        session: Session | None = None,
    ) -> None:
        self.run_id = run_id
        self.trace_id = trace_id
        self.project = project
        self.task_type = task_type
        self.agent_version = agent_version
        self.session = session
        self.start_time = time.time()
        self.spans: list[SpanRecord] = []
        self._span_stack: list[str] = []
        self.status = RunStatus.succeeded.value
        self.failure_code: str | None = None
        self.error_message: str | None = None
        self.input_hash = ""
        self.output_hash: str | None = None
        self.outputs: Any = None
        self.artifact_uris: list[str] = []
        self.cost_cents = 0.0
        self.user_feedback: dict[str, Any] | None = None
        self.redactor = TraceRedactor()

    @property
    def id(self) -> str:
        return self.run_id

    @property
    def run(self) -> RunContext:
        return self

    @property
    def current_parent_id(self) -> str | None:
        return self._span_stack[-1] if self._span_stack else None

    @contextmanager
    def span(
        self,
        arg1: str,
        arg2: str | None = None,
        attributes: dict[str, Any] | None = None,
        input_data: Any = None,
    ) -> Generator[SpanRecord, None, None]:
        """Create and context-manage a nested child execution span."""
        # Support both span("name", span_type="tool") and span("span_type", "name")
        if arg2 is not None:
            span_type = arg1
            name = arg2
        else:
            name = arg1
            span_type = "execute_tool"

        span_id = str(uuid.uuid4())
        rec = SpanRecord(
            id=span_id,
            run_id=self.run_id,
            parent_id=self.current_parent_id,
            span_type=span_type,
            name=name,
            attributes=attributes or {},
            input=input_data,
        )
        self.spans.append(rec)
        self._span_stack.append(span_id)
        try:
            yield rec
            rec.finish()
        except Exception as exc:
            rec.finish(error=exc)
            raise
        finally:
            self._span_stack.pop()

    def set_output(self, output: Any) -> None:
        """Set output data and compute output hash."""
        self.outputs = output
        try:
            serialized = json.dumps(output, sort_keys=True)
            self.output_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        except Exception:
            self.output_hash = hashlib.sha256(str(output).encode("utf-8")).hexdigest()

    def add_artifact(self, uri: str) -> None:
        """Attach an artifact URI (such as screenshot or confirmation document)."""
        if uri not in self.artifact_uris:
            self.artifact_uris.append(uri)

    def record_tool_call(
        self,
        tool_name: str,
        inputs: Any = None,
        outputs: Any = None,
        error: Exception | str | None = None,
        latency_ms: int = 0,
        attributes: dict[str, Any] | None = None,
    ) -> SpanRecord:
        """Record a completed tool execution directly as a span."""
        span_id = str(uuid.uuid4())
        attrs = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool_name,
            **(attributes or {}),
        }
        rec = SpanRecord(
            id=span_id,
            run_id=self.run_id,
            parent_id=self.current_parent_id,
            span_type="execute_tool",
            name=f"execute_tool {tool_name}",
            attributes=attrs,
            input=inputs,
            output=outputs,
            latency_ms=latency_ms,
        )
        if error:
            rec.finish(error=error)
        else:
            rec.end_time = time.time()
        self.spans.append(rec)
        return rec

    def record_guardrail(
        self,
        guardrail_name: str,
        passed: bool,
        rationale: str | None = None,
        inputs: Any = None,
    ) -> SpanRecord:
        """Record a safety or truth-guard validation check."""
        span_id = str(uuid.uuid4())
        attrs = {
            "gen_ai.operation.name": "verify_guardrail",
            "guardrail.name": guardrail_name,
            "guardrail.passed": passed,
        }
        rec = SpanRecord(
            id=span_id,
            run_id=self.run_id,
            parent_id=self.current_parent_id,
            span_type="verify_guardrail",
            name=f"verify_guardrail {guardrail_name}",
            attributes=attrs,
            input=inputs,
            output={"passed": passed, "rationale": rationale},
            error_type=None if passed else "GuardrailViolation",
            error_message=None if passed else rationale,
        )
        rec.finish()
        self.spans.append(rec)
        if not passed:
            self.status = RunStatus.guardrail_blocked.value
            self.failure_code = f"GUARDRAIL_{guardrail_name.upper()}"
            self.error_message = rationale
        return rec

    def set_failure(self, failure_code: str, error_message: str) -> None:
        """Mark the run as failed with a standardized taxonomy failure code."""
        self.status = RunStatus.failed.value
        self.failure_code = failure_code
        self.error_message = error_message

    def fail_run(self, failure_code: str, error_message: str) -> None:
        """Alias for set_failure."""
        self.set_failure(failure_code, error_message)


class ImprovementRuntime:
    """Entry point for executing agentic tasks under autonomous self-improvement monitoring."""

    def __init__(self, engine=None, session: Session | None = None, project: str = "hunterxjob") -> None:
        self.engine = engine or default_engine
        self.session = session
        self.project = project

    @contextmanager
    def start_run(
        self,
        task_type: str = "application",
        agent_version: str | None = None,
        inputs: Any = None,
    ) -> Generator[RunContext, None, None]:
        with self.trace(
            project=self.project,
            task_type=task_type,
            agent_version=agent_version,
            inputs=inputs,
            session=self.session,
        ) as ctx:
            yield ctx

    @contextmanager
    def trace(
        self,
        project: str = "hunterxjob",
        task_type: str = "application",
        agent_version: str | None = None,
        inputs: Any = None,
        session: Session | None = None,
    ) -> Generator[RunContext, None, None]:
        """Wrap an agent task execution in a hierarchical, privacy-scrubbed GenAI trace."""
        run_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())

        local_session = session or self.session or Session(bind=self.engine)
        owns_session = (session is None and self.session is None)

        try:
            if not agent_version:
                champ = get_current_champion(local_session, project=project)
                version_str = champ.version
            else:
                version_str = agent_version

            ctx = RunContext(
                run_id=run_id,
                trace_id=trace_id,
                project=project,
                task_type=task_type,
                agent_version=version_str,
                session=local_session,
            )

            # Compute input hash
            inp_str = json.dumps(inputs, sort_keys=True) if inputs is not None else ""
            ctx.input_hash = hashlib.sha256(inp_str.encode("utf-8")).hexdigest()

            # Create root agent span
            root_span_id = str(uuid.uuid4())
            root_span = SpanRecord(
                id=root_span_id,
                run_id=run_id,
                parent_id=None,
                span_type="invoke_agent",
                name=f"invoke_agent {project}",
                attributes={
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": project,
                    "gen_ai.agent.version": version_str,
                    "task.type": task_type,
                },
                input=ctx.redactor.scrub_object(inputs) if inputs is not None else None,
            )
            ctx.spans.append(root_span)
            ctx._span_stack.append(root_span_id)

            try:
                yield ctx
            except Exception as exc:
                ctx.set_failure(
                    failure_code=getattr(exc, "failure_code", "WORKFLOW_UNHANDLED_EXCEPTION"),
                    error_message=str(exc),
                )
                root_span.finish(error=exc)
                raise
            else:
                root_span.finish()
            finally:
                total_latency_ms = int((time.time() - ctx.start_time) * 1000)

                # Scrub and persist spans and run record
                scrubbed_spans = []
                for s in ctx.spans:
                    scrubbed_spans.append({
                        "id": s.id,
                        "run_id": s.run_id,
                        "parent_id": s.parent_id,
                        "span_type": s.span_type,
                        "name": s.name,
                        "attributes": ctx.redactor.scrub_object(s.attributes),
                        "input": ctx.redactor.scrub_object(s.input),
                        "output": ctx.redactor.scrub_object(s.output),
                        "error_type": s.error_type,
                        "error_message": ctx.redactor.scrub_text(s.error_message) if s.error_message else None,
                        "latency_ms": s.latency_ms,
                    })

                record_run(
                    session=local_session,
                    run_id=ctx.run_id,
                    trace_id=ctx.trace_id,
                    project=ctx.project,
                    task_type=ctx.task_type,
                    agent_version=ctx.agent_version,
                    status=ctx.status,
                    input_hash=ctx.input_hash,
                    output_hash=ctx.output_hash,
                    artifact_uris=ctx.artifact_uris,
                    cost_cents=ctx.cost_cents,
                    latency_ms=total_latency_ms,
                    failure_code=ctx.failure_code,
                    error_message=ctx.redactor.scrub_text(ctx.error_message) if ctx.error_message else None,
                    user_feedback=ctx.user_feedback,
                )
                record_trace_spans(local_session, scrubbed_spans)

                # Enqueue asynchronous grading job
                enqueue_job(
                    session=local_session,
                    job_type="grade_run",
                    payload={"run_id": ctx.run_id, "project": ctx.project},
                )
        finally:
            if owns_session:
                local_session.close()


improvement_runtime = ImprovementRuntime()
