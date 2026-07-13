from app.config import Settings
from app.discovery import clean_html
from app.models import Job
from app.pipeline import deterministic_gate


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
