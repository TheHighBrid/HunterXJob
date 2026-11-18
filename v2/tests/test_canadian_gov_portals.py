"""Tests for Canadian Federal, Provincial, and Municipal Government Job Portal Adapters 🇨🇦

Covers:
1. GC Jobs (Federal - jobs.gc.ca)
2. City of Ottawa Careers (Municipal - ottawa.ca / HRsmart)
3. Ontario Public Service (Provincial - gojobs.gov.on.ca)
4. Ville de Gatineau (Municipal - gatineau.ca / cvmanager.com)
5. Québec Emploi (Provincial - quebecemploi.gouv.qc.ca)
"""
from __future__ import annotations

from pathlib import Path
import pytest

from app.adapter_runtime import ADAPTER_CATALOG, REGISTRY, detect_platform, platform_for_job
from app.canadian_gov_portals import (
    GatineauCareersJobRef,
    GCJobsJobRef,
    OPSCareersJobRef,
    OttawaCareersJobRef,
    QuebecEmploiJobRef,
    fetch_canadian_gov_form,
    get_portal_credentials,
    get_portal_storage_state,
    make_canadian_gov_controls,
    parse_gatineau_ref,
    parse_gc_jobs_ref,
    parse_ops_ref,
    parse_ottawa_ref,
    parse_quebec_emploi_ref,
)
from app.config import Settings
from app.evidence import CONFIRMATION_MARKERS
from app.form_engine import ControlType
from app.real_forms import SUPPORTED_PLATFORMS, LiveFormProvider
from app.submission_engine import SUBMIT_BUTTON_SELECTORS


class MockJob:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_canadian_gov_url_detection():
    """Verify platform detection across Canadian Federal, Provincial, and Municipal portals."""
    # GC Jobs (Federal)
    assert detect_platform("https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1800?poster=123456&toggleLanguage=en") == "gc_jobs"
    assert detect_platform("https://jobs.gc.ca/jobs/preview/987654") == "gc_jobs"

    # City of Ottawa (Municipal)
    assert detect_platform("https://cityofottawa.hua.hrsmart.com/hr/ats/Posting/view/1001") == "ottawa_careers"
    assert detect_platform("https://ottawa.ca/en/city-hall/jobs-city/hrsmart-portal") == "ottawa_careers"

    # Ontario Public Service (Provincial)
    assert detect_platform("https://www.gojobs.gov.on.ca/Preview.aspx?JobID=19999") == "ops_careers"
    assert detect_platform("https://www.gojobs.gov.on.ca/JobOpportunities.aspx") == "ops_careers"

    # Ville de Gatineau (Municipal)
    assert detect_platform("https://gatineau.cvmanager.com/cvm5/display.asp?id=888") == "gatineau_careers"
    assert detect_platform("https://www.gatineau.ca/portail/default.aspx?p=guichet_municipal/emploi_carrieres") == "gatineau_careers"

    # Québec Emploi (Provincial)
    assert detect_platform("https://quebecemploi.gouv.qc.ca/offre-emploi/555555") == "quebec_emploi"


def test_canadian_gov_ref_parsers():
    """Verify URL parsers extract exact reference identifiers and vault scopes."""
    # GC Jobs
    gc_ref = parse_gc_jobs_ref("https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1800?poster=98765")
    assert gc_ref is not None
    assert gc_ref.poster_id == "98765"
    assert "gc_jobs:98765" in gc_ref.vault_scopes

    # City of Ottawa
    ott_ref = parse_ottawa_ref("https://cityofottawa.hua.hrsmart.com/hr/ats/Posting/view/2026-FIN-01")
    assert ott_ref is not None
    assert ott_ref.job_id == "2026-FIN-01"
    assert "ottawa_careers:2026-FIN-01" in ott_ref.vault_scopes

    # OPS
    ops_ref = parse_ops_ref("https://www.gojobs.gov.on.ca/Preview.aspx?JobID=182901")
    assert ops_ref is not None
    assert ops_ref.job_id == "182901"
    assert "ops_careers:182901" in ops_ref.vault_scopes

    # Gatineau
    gat_ref = parse_gatineau_ref("https://gatineau.cvmanager.com/cvm5/display.asp?id=3456")
    assert gat_ref is not None
    assert gat_ref.position_id == "3456"
    assert "gatineau_careers:3456" in gat_ref.vault_scopes

    # Québec Emploi
    qc_ref = parse_quebec_emploi_ref("https://quebecemploi.gouv.qc.ca/offre-emploi/778899")
    assert qc_ref is not None
    assert qc_ref.offer_id == "778899"
    assert "quebec_emploi:778899" in qc_ref.vault_scopes


def test_canadian_gov_form_controls_and_screening():
    """Verify that government controls enforce Canadian citizenship preference, bilingualism, and security clearance."""
    gc_controls = make_canadian_gov_controls("gc_jobs")
    keys = {c.key: c for c in gc_controls}

    # Citizenship / PSEA preference
    assert "canadian_citizenship" in keys
    cit_ctrl = keys["canadian_citizenship"]
    assert cit_ctrl.required is True
    assert cit_ctrl.legal is True
    assert any("Citoyen canadien" in opt for opt in cit_ctrl.options)

    # Official Languages / Bilingualism
    assert "language_proficiency" in keys
    lang_ctrl = keys["language_proficiency"]
    assert lang_ctrl.required is True
    assert any("Bilingual / Bilingue" in opt for opt in lang_ctrl.options)

    # Security Clearance (Fail-Closed)
    assert "security_clearance" in keys
    sec_ctrl = keys["security_clearance"]
    assert sec_ctrl.required is True
    assert sec_ctrl.legal is True
    assert any("Reliability" in opt for opt in sec_ctrl.options)
    assert any("Secret" in opt for opt in sec_ctrl.options)

    # Employment Equity in GC Jobs
    assert "psea_self_declaration" in keys
    ee_ctrl = keys["psea_self_declaration"]
    assert ee_ctrl.sensitive is True
    assert any("Visible Minority" in opt for opt in ee_ctrl.options)


def test_live_form_provider_canadian_gov_routing():
    """Verify LiveFormProvider routes Canadian government jobs to real form representations."""
    settings = Settings()
    provider = LiveFormProvider(settings)

    # GC Jobs
    job = MockJob(
        source="gc_jobs",
        external_id="123456",
        title="Senior Risk Analyst / Analyste principal des risques",
        url="https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1800?poster=123456",
    )
    form = provider.fetch(job)
    assert form.platform == "gc_jobs"
    assert form.title == "Senior Risk Analyst / Analyste principal des risques"
    assert any(c.key == "security_clearance" for c in form.controls)
    assert any(c.key == "canadian_citizenship" for c in form.controls)


def test_canadian_gov_storage_state_and_credentials(monkeypatch, tmp_path):
    """Test session state lookup and credential retrieval for account-based portals."""
    monkeypatch.setenv("GC_JOBS_USERNAME", "mohamed.alem@example.ca")
    monkeypatch.setenv("GC_JOBS_PASSWORD", "SuperSecureGovPass2026!")

    user, pwd = get_portal_credentials("gc_jobs")
    assert user == "mohamed.alem@example.ca"
    assert pwd == "SuperSecureGovPass2026!"

    # Session storage state file check
    session_file = tmp_path / "gc_jobs_storage_state.json"
    session_file.write_text('{"cookies": [], "origins": []}')

    state = get_portal_storage_state("gc_jobs", session_dir=tmp_path)
    assert state == session_file


def test_canadian_gov_submit_and_confirmation_markers():
    """Verify French and Canadian government submit buttons and receipt banners."""
    assert any("Soumettre la demande" in s for s in SUBMIT_BUTTON_SELECTORS)
    assert any("Postuler" in s for s in SUBMIT_BUTTON_SELECTORS)

    assert any("votre candidature a été soumise" in m for m in CONFIRMATION_MARKERS)
    assert any("merci d'avoir postulé" in m for m in CONFIRMATION_MARKERS)
    assert any("candidature reçue" in m for m in CONFIRMATION_MARKERS)


def test_canadian_gov_adapter_catalog():
    """Verify all 5 Canadian government portals are registered in ADAPTER_CATALOG."""
    for portal in ("gc_jobs", "ottawa_careers", "ops_careers", "gatineau_careers", "quebec_emploi"):
        assert portal in ADAPTER_CATALOG
        assert portal in REGISTRY.names()
        assert portal in SUPPORTED_PLATFORMS
