from __future__ import annotations

from app.models import PipelineStage

ALLOWED: dict[str, frozenset[str]] = {
    PipelineStage.discovered.value: frozenset({
        PipelineStage.normalized.value,
        PipelineStage.eligible.value,
        PipelineStage.review.value,
        PipelineStage.rejected.value,
        PipelineStage.failed.value,
    }),
    PipelineStage.normalized.value: frozenset({
        PipelineStage.eligible.value,
        PipelineStage.review.value,
        PipelineStage.rejected.value,
        PipelineStage.failed.value,
    }),
    PipelineStage.eligible.value: frozenset({
        PipelineStage.scored.value,
        PipelineStage.review.value,
        PipelineStage.rejected.value,
        PipelineStage.failed.value,
    }),
    PipelineStage.review.value: frozenset({
        PipelineStage.eligible.value,
        PipelineStage.shortlisted.value,
        PipelineStage.rejected.value,
        PipelineStage.needs_review.value,
    }),
    PipelineStage.scored.value: frozenset({
        PipelineStage.shortlisted.value,
        PipelineStage.rejected.value,
        PipelineStage.review.value,
    }),
    PipelineStage.shortlisted.value: frozenset({
        PipelineStage.approved.value,
        PipelineStage.preparing.value,
        PipelineStage.materials_generated.value,
        PipelineStage.rejected.value,
        PipelineStage.withdrawn.value,
    }),
    PipelineStage.approved.value: frozenset({
        PipelineStage.preparing.value,
        PipelineStage.materials_generated.value,
        PipelineStage.ready_to_apply.value,
        PipelineStage.withdrawn.value,
    }),
    PipelineStage.preparing.value: frozenset({
        PipelineStage.materials_generated.value,
        PipelineStage.failed.value,
        PipelineStage.needs_review.value,
    }),
    PipelineStage.materials_generated.value: frozenset({
        PipelineStage.materials_reviewed.value,
        PipelineStage.ready_to_apply.value,
        PipelineStage.needs_review.value,
        PipelineStage.failed.value,
    }),
    PipelineStage.materials_reviewed.value: frozenset({
        PipelineStage.ready_to_apply.value,
        PipelineStage.needs_review.value,
        PipelineStage.withdrawn.value,
    }),
    PipelineStage.ready_to_apply.value: frozenset({
        PipelineStage.applying.value,
        PipelineStage.needs_review.value,
        PipelineStage.withdrawn.value,
    }),
    PipelineStage.applying.value: frozenset({
        PipelineStage.form_filled.value,
        PipelineStage.validated.value,
        PipelineStage.needs_review.value,
        PipelineStage.failed.value,
        PipelineStage.submission_uncertain.value,
        PipelineStage.submitted.value,
    }),
    PipelineStage.form_filled.value: frozenset({
        PipelineStage.validated.value,
        PipelineStage.needs_review.value,
        PipelineStage.failed.value,
    }),
    PipelineStage.validated.value: frozenset({
        PipelineStage.submitted.value,
        PipelineStage.submission_uncertain.value,
        PipelineStage.needs_review.value,
        PipelineStage.ready_to_apply.value,
    }),
    PipelineStage.needs_review.value: frozenset({
        PipelineStage.applying.value,
        PipelineStage.ready_to_apply.value,
        PipelineStage.rejected.value,
        PipelineStage.withdrawn.value,
        PipelineStage.failed.value,
        PipelineStage.submission_uncertain.value,
    }),
    PipelineStage.submission_uncertain.value: frozenset({
        PipelineStage.submitted.value,
        PipelineStage.confirmed.value,
        PipelineStage.needs_review.value,
        PipelineStage.failed.value,
        PipelineStage.withdrawn.value,
    }),
    PipelineStage.submitted.value: frozenset({
        PipelineStage.confirmed.value,
        PipelineStage.submission_uncertain.value,
        PipelineStage.needs_review.value,
    }),
    PipelineStage.confirmed.value: frozenset(),
    PipelineStage.rejected.value: frozenset(),
    PipelineStage.withdrawn.value: frozenset(),
    PipelineStage.failed.value: frozenset({
        PipelineStage.ready_to_apply.value,
        PipelineStage.preparing.value,
        PipelineStage.needs_review.value,
    }),
}


class IllegalTransition(ValueError):
    pass


def can_transition(from_stage: str | None, to_stage: str) -> bool:
    if from_stage is None:
        return True
    if from_stage == to_stage:
        return True
    allowed = ALLOWED.get(from_stage)
    return bool(allowed and to_stage in allowed)


def assert_transition(from_stage: str | None, to_stage: str) -> None:
    if not can_transition(from_stage, to_stage):
        raise IllegalTransition(f"illegal transition {from_stage} -> {to_stage}")
