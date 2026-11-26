"""Generic fraud-search postings used by the targeting regression tests.

Titles mirror a real "fraud jobs" search result list. Companies, locations and
descriptions are generic placeholders (no candidate or employer data).
"""
from __future__ import annotations

from app.decisioning import JobFacts

_BASE = (
    "You will review card transactions, investigate suspicious activity, document findings and "
    "escalate confirmed fraud, disputes and chargebacks. You will work with banking operations, "
    "AML and KYC partners, follow regulatory procedures and keep case notes accurate."
)
_BILINGUAL = " Bilingual French and English communication is required for client calls."


def _job(title: str, location: str, *, remote: bool, extra: str = "", bilingual: bool = False) -> JobFacts:
    description = _BASE + (" " + extra if extra else "") + (_BILINGUAL if bilingual else "")
    return JobFacts(title=title, company="Example Co", location=location, description=description, remote=remote)


FRAUD_SEARCH_POSTINGS: tuple[tuple[str, JobFacts], ...] = (
    ("core", _job("Associate, Fraud Investigations Cards and Disputes (Fixed Term Contract)", "Remote, Canada", remote=True)),
    ("core", _job("Bilingual Fraud Investigation Agent", "Remote, Canada", remote=True, bilingual=True)),
    ("core", _job("Remote Fraud Specialist, Supplier Operations & Governance", "Remote, Canada", remote=True)),
    ("sales", _job("Account Manager for SaaS Fraud Solutions", "Remote, Canada", remote=True,
                   extra="You will manage a portfolio of fraud prevention software clients.")),
    ("leadership", _job("Senior Manager Inbound Fraud Operations", "Remote, Canada", remote=True,
                        extra="You will lead a team of fraud operations agents.")),
    ("core", _job("Universal Fraud Agent, Remote - Bilingual", "Remote, Canada", remote=True, bilingual=True)),
    ("core", _job("Bilingual Fraud Advisor Advice Centre- EN", "Remote, Canada", remote=True, bilingual=True)),
    ("core", _job("Bilingual Fraud Advisor Advice Centre", "Ottawa, ON", remote=False, bilingual=True)),
    ("leadership", _job("Head of Fraud Operations Remote", "Remote, Canada", remote=True,
                        extra="You will lead fraud operations strategy and managers.")),
    ("data_science", _job("Data Scientist, Risk & Fraud", "Remote, Canada", remote=True,
                          extra="You will build fraud risk models with Python and SQL.")),
    ("core", _job("Bilingual Fraud Advisor Advice Centre", "Remote, Canada", remote=True, bilingual=True)),
    ("adjacent", _job("Senior Insurance Claims Investigator", "Remote, Canada", remote=True,
                      extra="You will investigate suspected insurance claim fraud.")),
    ("data_science", _job("Data Scientist: Risk & Fraud Focus", "Remote, Canada", remote=True,
                          extra="You will build fraud detection models with Python and SQL.")),
    ("sales", _job("Strategic Account Manager", "Toronto, Ontario", remote=False,
                   extra="You will grow strategic accounts for a fraud prevention platform.")),
    ("adjacent", _job("Private Investigator - Surveillance - Full-Time", "Remote, Canada", remote=True,
                      extra="You will conduct surveillance on suspected fraud cases.")),
)
