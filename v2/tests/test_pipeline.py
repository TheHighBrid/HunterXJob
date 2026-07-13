import pytest

from app.pipeline import PipelineStage, transition


def test_valid_forward_transition() -> None:
    result = transition(PipelineStage.DISCOVERED, PipelineStage.NORMALIZED)
    assert result.previous is PipelineStage.DISCOVERED
    assert result.current is PipelineStage.NORMALIZED


def test_invalid_stage_skip_is_rejected() -> None:
    with pytest.raises(ValueError):
        transition(PipelineStage.DISCOVERED, PipelineStage.SCORED)


def test_needs_review_is_allowed_from_active_stage() -> None:
    result = transition(PipelineStage.SCORED, PipelineStage.NEEDS_REVIEW)
    assert result.current is PipelineStage.NEEDS_REVIEW


def test_terminal_stage_cannot_resume_automatically() -> None:
    with pytest.raises(ValueError):
        transition(PipelineStage.FAILED, PipelineStage.DISCOVERED)
