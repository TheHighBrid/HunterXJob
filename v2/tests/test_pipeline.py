from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.decisioning import Decision
from app.discovery import clean_html
from app.models import Application, Job, PipelineStage
from app.pipeline import deterministic_gate, score_pending_jobs


def test_clean_html_removes_markup():
    assert clean_html("<p>Hello &amp; welcome</p><ul><li>Fraud</li></ul>") == "Hello & welcome\nFraud"


def test_rejects_excluded_location():
    settings = Settings(
        target_locations="Ottawa,Remote Canada,Canada",
        target_keywords="fraud,compliance",
        excluded_locations="United States,US Remote",
    )
    job = Job(
        source="test",
        external_id="1",
        title="Fraud Analyst",
        company="Example",
        location="US Remote",
        url="https://example.test",
        description="Fraud and compliance operations",
    )
    result = deterministic_gate(job, settings)
    assert result.eligible is False
    assert result.decision is Decision.REJECT
    assert result.reason == "excluded location"


def test_accepts_relevant_canadian_job():
    settings = Settings(
        target_locations="Ottawa,Remote Canada,Canada",
        target_keywords="fraud,compliance,bilingual",
        excluded_locations="United States,US Remote",
        excluded_titles="software engineer",
    )
    job = Job(
        source="test",
        external_id="2",
        title="Bilingual Fraud Analyst",
        company="Canadian Bank",
        location="Ottawa, Ontario, Canada",
        url="https://example.test",
        description=(
            "Investigate fraud alerts, follow AML and compliance controls, document evidence, "
            "support credit-card disputes, and communicate with customers in English and French. "
            "The analyst works with banking operations and payment-risk teams across Canada."
        ),
    )
    result = deterministic_gate(job, settings)
    assert result.eligible is True
    assert result.decision is Decision.SHORTLIST
    assert result.score >= 60


def _memory_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_review_job_does_not_advance_to_scoring_or_application():
    db = _memory_db()
    settings = Settings(
        target_locations="Ottawa,Canada",
        target_keywords="fraud",
        excluded_locations="United States",
        excluded_titles="software engineer",
        min_match_score=60,
    )
    job = Job(
        source="test",
        external_id="director-1",
        title="Director of Fraud Operations",
        company="Canadian Bank",
        location="Ottawa, Ontario, Canada",
        url="https://boards.greenhouse.io/example/jobs/1",
        description="Lead a fraud operations team and set strategy.",
        stage=PipelineStage.discovered.value,
    )
    db.add(job)
    db.commit()

    processed = score_pending_jobs(db, settings, resume_facts="fraud analyst in Ottawa", use_ai=False)
    db.refresh(job)

    assert processed == 1
    assert job.stage == PipelineStage.review.value
    assert job.ai_score is None
    assert db.execute(select(Application)).scalars().first() is None
