"""score_job_fit should give a better-matching resume a higher score."""
from app.services.matching import score_job_fit

JOB_DESCRIPTION = """
We are looking for a Senior Backend Engineer with strong experience in
Python, FastAPI, PostgreSQL, and distributed systems. You will design and
build scalable microservices, own on-call rotations, and mentor junior
engineers. Experience with Kubernetes and AWS is a strong plus.
"""

GOOD_RESUME = """
Senior Backend Engineer with 6 years of experience building scalable
microservices in Python and FastAPI. Deep expertise in PostgreSQL query
optimization, distributed systems design, Kubernetes deployments, and AWS
infrastructure. Led on-call rotations and mentored junior engineers.
"""

BAD_RESUME = """
Graphic designer with a passion for Adobe Photoshop, Illustrator, and brand
identity design. Experience creating marketing collateral and social media
graphics for small businesses.
"""


def test_better_match_scores_higher():
    good_score = score_job_fit(GOOD_RESUME, JOB_DESCRIPTION)
    bad_score = score_job_fit(BAD_RESUME, JOB_DESCRIPTION)
    assert good_score > bad_score


def test_score_is_in_valid_range():
    score = score_job_fit(GOOD_RESUME, JOB_DESCRIPTION)
    assert 0.0 <= score <= 100.0
    score2 = score_job_fit(BAD_RESUME, JOB_DESCRIPTION)
    assert 0.0 <= score2 <= 100.0


def test_empty_inputs_do_not_crash():
    assert score_job_fit("", "") == 0.0
    assert score_job_fit("some resume text", "") == 0.0
    assert score_job_fit("", "some job description") == 0.0


def test_identical_text_scores_highly():
    score = score_job_fit(JOB_DESCRIPTION, JOB_DESCRIPTION)
    assert score > 90.0
