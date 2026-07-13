from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PipelineStage(StrEnum):
    DISCOVERED = "discovered"
    NORMALIZED = "normalized"
    ELIGIBILITY_CHECKED = "eligibility_checked"
    SCORED = "scored"
    SHORTLISTED = "shortlisted"
    MATERIALS_GENERATED = "materials_generated"
    MATERIALS_REVIEWED = "materials_reviewed"
    READY_TO_APPLY = "ready_to_apply"
    FORM_FILLED = "form_filled"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


ORDERED_STAGES = [
    PipelineStage.DISCOVERED,
    PipelineStage.NORMALIZED,
    PipelineStage.ELIGIBILITY_CHECKED,
    PipelineStage.SCORED,
    PipelineStage.SHORTLISTED,
    PipelineStage.MATERIALS_GENERATED,
    PipelineStage.MATERIALS_REVIEWED,
    PipelineStage.READY_TO_APPLY,
    PipelineStage.FORM_FILLED,
    PipelineStage.VALIDATED,
    PipelineStage.SUBMITTED,
    PipelineStage.CONFIRMED,
]


@dataclass(frozen=True)
class TransitionResult:
    previous: PipelineStage
    current: PipelineStage


def transition(current: PipelineStage, target: PipelineStage) -> TransitionResult:
    if target in {PipelineStage.NEEDS_REVIEW, PipelineStage.FAILED}:
        return TransitionResult(current, target)

    if current in {PipelineStage.NEEDS_REVIEW, PipelineStage.FAILED}:
        raise ValueError(f"Cannot automatically transition from terminal stage {current}")

    current_index = ORDERED_STAGES.index(current)
    target_index = ORDERED_STAGES.index(target)

    if target_index != current_index + 1:
        raise ValueError(f"Invalid transition: {current} -> {target}")

    return TransitionResult(current, target)
