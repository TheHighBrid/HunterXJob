from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
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
        description="Investigate fraud and follow compliance controls in English and French.",
    )
    result = deterministic_gate(job, settings)
    assert result.eligible is True
    assert result.score >= 60


def test_review_job_does_not_advance_to_scoring_or_application():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        target_locations="Ottawa,Remote Canada,Canada",
        target_keywords="fraud,compliance",
        min_match_score=10,
    )

    with session_factory() as db:
        job = Job(
            source="test",
            external_id="review-1",
            title="Director of Fraud",
            company="Canadian Bank",
            location="Ottawa, Canada",
            url="https://example.test/review-1",
            description="Lead fraud and compliance.",
        )
        db.add(job)
        db.commit()

        assert score_pending_jobs(db, settings, "resume facts", use_ai=False) == 1
        db.refresh(job)

        assert job.eligible is False
        assert job.stage == PipelineStage.review.value
        assert job.ai_score is None
        assert db.execute(select(Application).where(Application.job_id == job.id)).scalar_one_or_none() is None
