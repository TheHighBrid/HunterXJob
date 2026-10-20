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
    PipelineStage.duplicate.value: frozenset(),
    PipelineStage.closed.value: frozenset(),
}


# Duplicate links and closed postings (see app.dedup and app.job_liveness).
# Neither is reachable from an in-flight or submission stage.
_DEDUP_SOURCES = (
    PipelineStage.discovered, PipelineStage.normalized, PipelineStage.eligible, PipelineStage.review,
    PipelineStage.scored, PipelineStage.shortlisted, PipelineStage.approved, PipelineStage.materials_generated,
    PipelineStage.materials_reviewed, PipelineStage.ready_to_apply, PipelineStage.needs_review, PipelineStage.failed,
)
_CLOSE_SOURCES = (*_DEDUP_SOURCES, PipelineStage.validated, PipelineStage.duplicate)

for _stage in _DEDUP_SOURCES:
    ALLOWED[_stage.value] = ALLOWED[_stage.value] | {PipelineStage.duplicate.value}
for _stage in _CLOSE_SOURCES:
    ALLOWED[_stage.value] = ALLOWED.get(_stage.value, frozenset()) | {PipelineStage.closed.value}
# The owner can unlink a duplicate (re-gated from scratch); a closed posting
# that a later definitive check finds live again is reopened the same way.
ALLOWED[PipelineStage.duplicate.value] = ALLOWED[PipelineStage.duplicate.value] | {PipelineStage.discovered.value}
ALLOWED[PipelineStage.closed.value] = frozenset({PipelineStage.discovered.value})

#: Stages from which a job can never again be prepared or dry-run without re-gating.
INACTIVE_STAGES = frozenset({PipelineStage.duplicate.value, PipelineStage.closed.value})


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
