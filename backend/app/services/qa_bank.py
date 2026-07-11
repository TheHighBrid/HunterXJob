"""Q&A answer bank: fuzzy-match recurring application questions to stored answers.

Recurring application questions ("Are you authorized to work in the US?",
"Years of experience with Python?") are stored as pattern -> answer pairs and
reused/refined across applications rather than regenerated from scratch every
time. Matching uses difflib.SequenceMatcher (stdlib only, no extra deps).
"""
from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import QAAnswer


def _normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


class QAAnswerBank:
    """Wraps DB-backed Q&A pattern -> answer storage with fuzzy lookup."""

    def __init__(self, db: Session, match_threshold: float = 0.6):
        self.db = db
        self.match_threshold = match_threshold

    def match_question(self, question_text: str) -> QAAnswer | None:
        """Return the best-matching stored QAAnswer for `question_text`, or None."""
        candidates = self.db.execute(select(QAAnswer)).scalars().all()
        if not candidates:
            return None

        best: QAAnswer | None = None
        best_score = 0.0
        for candidate in candidates:
            score = _similarity(question_text, candidate.question_pattern)
            if score > best_score:
                best_score = score
                best = candidate

        if best is not None and best_score >= self.match_threshold:
            best.usage_count += 1
            self.db.add(best)
            self.db.commit()
            self.db.refresh(best)
            return best
        return None

    def save_answer(
        self, pattern: str, answer: str, tone_variant: str = "neutral"
    ) -> QAAnswer:
        """Store a new pattern -> answer, or update an existing near-identical pattern."""
        existing = self.match_question(pattern)
        if existing is not None and _similarity(pattern, existing.question_pattern) >= 0.9:
            existing.answer_text = answer
            existing.tone_variant = tone_variant
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        qa = QAAnswer(question_pattern=pattern, answer_text=answer, tone_variant=tone_variant)
        self.db.add(qa)
        self.db.commit()
        self.db.refresh(qa)
        return qa
