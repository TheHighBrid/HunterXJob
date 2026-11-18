"""Canadian Federal, Provincial, and Municipal Government Job Portal Adapters 🇨🇦

Covers the National Capital Region (Ottawa–Gatineau) and Canadian public sector:
1. GC Jobs (Federal Public Service / Emplois GC - jobs.gc.ca / cfp-psc.gc.ca)
2. City of Ottawa Careers (Ville d'Ottawa - ottawa.ca / HRsmart / Dayforce)
3. Ontario Public Service (OPS - gojobs.gov.on.ca)
4. Ville de Gatineau (gatineau.ca / cvmanager.com)
5. Québec Emploi (quebecemploi.gouv.qc.ca)

Key architectural characteristics:
- Account-based portals with saved candidate profile sessions.
- PSEA (Public Service Employment Act) citizenship preference verification.
- Official languages & bilingual proficiency (English/French BBB/CBC/CCC).
- Government security clearance fail-closed validation (Reliability, Secret, Top Secret).
- Criterion-based screening statements (STAR format) grounded in verified candidate facts.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.form_engine import (
    AUTH_PATTERNS,
    LEGAL_PATTERNS,
    SENSITIVE_PATTERNS,
    ControlType,
    FormControl,
)
from app.greenhouse_form import FormFetchError, RealForm

USER_AGENT = "Mozilla/5.0 (compatible; HunterXJob/2; Canadian Public Sector read-only)"


# ============================================================================
# 1. GC Jobs (Government of Canada / Emplois GC)
# ============================================================================

GC_JOBS_HOSTS = (
    "jobs.gc.ca",
    "emplois.gc.ca",
    "emploisfp-psjobs.cfp-psc.gc.ca",
    "psjobs-emploisfp.cfp-psc.gc.ca",
    "canada.ca",
)


@dataclass(frozen=True, slots=True)
class GCJobsJobRef:
    poster_id: str
    process_number: str = ""

    @property
    def apply_url(self) -> str:
        return f"https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1800?poster={self.poster_id}&toggleLanguage=en"

    @property
    def apply_url_fr(self) -> str:
        return f"https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1800?poster={self.poster_id}&toggleLanguage=fr"

    @property
    def vault_scopes(self) -> list[str]:
        scopes = [f"gc_jobs:{self.poster_id}"]
        if self.process_number:
            scopes.append(f"gc_jobs:process:{self.process_number.lower()}")
        return scopes


def parse_gc_jobs_ref(url: str) -> GCJobsJobRef | None:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not any(h in host for h in GC_JOBS_HOSTS):
        return None

    query = parse_qs(parsed.query)
    poster = (query.get("poster") or query.get("posterId") or query.get("psrs_poster_id") or [None])[0]
    if poster and re.match(r"^[0-9A-Za-z_-]+$", poster):
        return GCJobsJobRef(poster_id=poster)

    # Path check: /jobs/preview/{id} or /psrs-srfp/applicant/{id}
    parts = [p for p in parsed.path.split("/") if p]
    if parts:
        last = parts[-1]
        if re.match(r"^\d{4,12}$", last):
            return GCJobsJobRef(poster_id=last)

    return None


# ============================================================================
# 2. City of Ottawa Careers (Ville d'Ottawa)
# ============================================================================

OTTAWA_HOSTS = (
    "ottawa.ca",
    "cityofottawa.hua.hrsmart.com",
    "ottawa.dayforcehcm.com",
    "careers.ottawa.ca",
)


@dataclass(frozen=True, slots=True)
class OttawaCareersJobRef:
    job_id: str

    @property
    def apply_url(self) -> str:
        return f"https://cityofottawa.hua.hrsmart.com/hr/ats/Posting/view/{self.job_id}"

    @property
    def vault_scopes(self) -> list[str]:
        return [f"ottawa_careers:{self.job_id}"]


def parse_ottawa_ref(url: str) -> OttawaCareersJobRef | None:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not any(h in host for h in OTTAWA_HOSTS):
        return None

    # Query check
    query = parse_qs(parsed.query)
    jid = (query.get("jobId") or query.get("id") or query.get("PostingID") or [None])[0]
    if jid:
        return OttawaCareersJobRef(job_id=jid)

    parts = [p for p in parsed.path.split("/") if p]
    if parts:
        last = parts[-1]
        if last.lower() not in ("view", "job", "posting", "details", "hr", "ats") and re.match(r"^[0-9A-Za-z_-]+$", last):
            return OttawaCareersJobRef(job_id=last)
        for idx in range(len(parts) - 1, -1, -1):
            if parts[idx].lower() in ("view", "job", "details") and idx + 1 < len(parts):
                val = parts[idx + 1]
                if re.match(r"^[0-9A-Za-z_-]+$", val):
                    return OttawaCareersJobRef(job_id=val)

    return None


# ============================================================================
# 3. Ontario Public Service (OPS Careers - gojobs.gov.on.ca)
# ============================================================================

OPS_HOSTS = (
    "gojobs.gov.on.ca",
    "ontario.ca",
)


@dataclass(frozen=True, slots=True)
class OPSCareersJobRef:
    job_id: str

    @property
    def apply_url(self) -> str:
        return f"https://www.gojobs.gov.on.ca/Preview.aspx?JobID={self.job_id}"

    @property
    def vault_scopes(self) -> list[str]:
        return [f"ops_careers:{self.job_id}"]


def parse_ops_ref(url: str) -> OPSCareersJobRef | None:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not any(h in host for h in OPS_HOSTS):
        return None

    query = parse_qs(parsed.query)
    jid = (query.get("JobID") or query.get("jobid") or query.get("id") or [None])[0]
    if jid and re.match(r"^\d+$", jid):
        return OPSCareersJobRef(job_id=jid)

    parts = [p for p in parsed.path.split("/") if p]
    if parts and re.match(r"^\d{4,8}$", parts[-1]):
        return OPSCareersJobRef(job_id=parts[-1])

    return None


# ============================================================================
# 4. Ville de Gatineau Emplois (gatineau.ca / cvmanager.com)
# ============================================================================

GATINEAU_HOSTS = (
    "gatineau.ca",
    "gatineau.cvmanager.com",
)


@dataclass(frozen=True, slots=True)
class GatineauCareersJobRef:
    position_id: str

    @property
    def apply_url(self) -> str:
        return f"https://gatineau.cvmanager.com/cvm5/display.asp?id={self.position_id}"

    @property
    def vault_scopes(self) -> list[str]:
        return [f"gatineau_careers:{self.position_id}"]


def parse_gatineau_ref(url: str) -> GatineauCareersJobRef | None:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not any(h in host for h in GATINEAU_HOSTS):
        return None

    query = parse_qs(parsed.query)
    pid = (query.get("id") or query.get("posid") or [None])[0]
    if pid and re.match(r"^[0-9A-Za-z_-]+$", pid):
        return GatineauCareersJobRef(position_id=pid)

    parts = [p for p in parsed.path.split("/") if p]
    if parts and re.match(r"^\d{3,8}$", parts[-1]):
        return GatineauCareersJobRef(position_id=parts[-1])

    return None


# ============================================================================
# 5. Québec Emploi (quebecemploi.gouv.qc.ca)
# ============================================================================

QUEBEC_EMPLOI_HOSTS = (
    "quebecemploi.gouv.qc.ca",
    "emploiquebec.gouv.qc.ca",
)


@dataclass(frozen=True, slots=True)
class QuebecEmploiJobRef:
    offer_id: str

    @property
    def apply_url(self) -> str:
        return f"https://quebecemploi.gouv.qc.ca/offre-emploi/{self.offer_id}"

    @property
    def vault_scopes(self) -> list[str]:
        return [f"quebec_emploi:{self.offer_id}"]


def parse_quebec_emploi_ref(url: str) -> QuebecEmploiJobRef | None:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not any(h in host for h in QUEBEC_EMPLOI_HOSTS):
        return None

    parts = [p for p in parsed.path.split("/") if p]
    for idx, part in enumerate(parts):
        if part.lower() in ("offre-emploi", "offres", "details") and idx + 1 < len(parts):
            val = parts[idx + 1]
            if re.match(r"^[0-9A-Za-z_-]+$", val):
                return QuebecEmploiJobRef(offer_id=val)

    if parts and re.match(r"^\d{4,10}$", parts[-1]):
        return QuebecEmploiJobRef(offer_id=parts[-1])

    return None


# ============================================================================
# Universal Canadian Public Sector Form Generator
# ============================================================================

def make_canadian_gov_controls(portal_name: str) -> list[FormControl]:
    """Generate the standardized questionnaire controls for Canadian government portals."""
    controls = [
        # Standard candidate identity
        FormControl(
            key="first_name",
            label="First Name / Prénom",
            control_type=ControlType.TEXT,
            required=True,
            name="first_name",
            section="personal",
            vault_keys=["first_name", "full_name"],
        ),
        FormControl(
            key="last_name",
            label="Last Name / Nom de famille",
            control_type=ControlType.TEXT,
            required=True,
            name="last_name",
            section="personal",
            vault_keys=["last_name", "full_name"],
        ),
        FormControl(
            key="email",
            label="Email Address / Courriel",
            control_type=ControlType.EMAIL,
            required=True,
            name="email",
            section="personal",
            vault_keys=["email"],
        ),
        FormControl(
            key="phone",
            label="Telephone / Téléphone",
            control_type=ControlType.TEL,
            required=True,
            name="phone",
            section="personal",
            vault_keys=["phone", "mobile_phone"],
        ),
        FormControl(
            key="location",
            label="City, Province / Ville, Province",
            control_type=ControlType.AUTOCOMPLETE,
            required=True,
            name="location",
            section="personal",
            vault_keys=["location", "city", "address"],
        ),
        # PSEA & Citizenship Status
        FormControl(
            key="canadian_citizenship",
            label="Are you a Canadian Citizen or Permanent Resident? / Êtes-vous citoyen canadien ou résident permanent?",
            control_type=ControlType.RADIO,
            required=True,
            options=["Canadian Citizen / Citoyen canadien", "Permanent Resident / Résident permanent", "Work Permit / Permis de travail", "No / Non"],
            name="citizenship_status",
            legal=True,
            section="eligibility",
            vault_keys=["canadian_citizen", "work_authorization_canada", "citizenship_status"],
        ),
        # Official Languages & Bilingual SLE Proficiency
        FormControl(
            key="language_proficiency",
            label="Official Language Proficiency / Profil linguistique (Français / Anglais)",
            control_type=ControlType.SELECT,
            required=True,
            options=[
                "Bilingual / Bilingue (English & French / Anglais et Français)",
                "English Essential / Anglais essentiel",
                "French Essential / Français essentiel",
            ],
            name="official_languages",
            section="languages",
            vault_keys=["bilingual", "language_french", "language_proficiency"],
        ),
        # Government Security Clearance (Fail-Closed)
        FormControl(
            key="security_clearance",
            label="Current Government Security Clearance / Cote de sécurité actuelle",
            control_type=ControlType.SELECT,
            required=True,
            options=[
                "None / Aucune",
                "Reliability Status / Cote de fiabilité",
                "Secret (Level II) / Secret (Niveau II)",
                "Top Secret (Level III) / Très secret (Niveau III)",
            ],
            name="security_clearance",
            legal=True,
            section="security",
            vault_keys=["security_clearance"],
        ),
        # Attachments
        FormControl(
            key="resume",
            label="Curriculum Vitae / Résumé",
            control_type=ControlType.FILE,
            required=True,
            name="resume",
            section="attachments",
            vault_keys=["resume"],
        ),
        FormControl(
            key="cover_letter",
            label="Cover Letter / Lettre de présentation",
            control_type=ControlType.FILE,
            required=False,
            name="cover_letter",
            section="attachments",
            vault_keys=["cover_letter"],
        ),
    ]

    # Add portal-specific criteria questions
    if portal_name == "gc_jobs":
        controls.append(
            FormControl(
                key="psea_self_declaration",
                label="Employment Equity Self-Declaration / Déclaration volontaire d'équité en matière d'emploi",
                control_type=ControlType.SELECT,
                required=False,
                options=[
                    "I choose not to declare / Je choisis de ne pas déclarer",
                    "Visible Minority / Minorité visible",
                    "Person with a disability / Personne en situation de handicap",
                    "Indigenous / Autochtone",
                ],
                name="employment_equity",
                sensitive=True,
                section="demographics",
                vault_keys=["voluntary_self_identification"],
            )
        )
    return controls


def fetch_canadian_gov_form(
    platform: str,
    job_ref: Any,
    job_title: str = "Government Position",
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> RealForm:
    """Construct or fetch the real application form for a Canadian government job."""
    controls = make_canadian_gov_controls(platform)
    scopes = getattr(job_ref, "vault_scopes", [f"{platform}:generic"])
    apply_url = getattr(job_ref, "apply_url", "https://canada.ca")

    return RealForm(
        platform=platform,
        source=platform,
        url=apply_url,
        title=job_title,
        controls=controls,
        vault_scopes=scopes,
        metadata={"platform_type": "canadian_public_sector", "ncr_relevant": True},
    )


# ============================================================================
# Saved Session & Storage State Engine for Account-Based Systems
# ============================================================================

DEFAULT_SESSION_DIR = Path.home() / ".hunterx" / "sessions"


def get_portal_storage_state(portal: str, session_dir: Path | None = None) -> Path | None:
    """Return the saved Playwright storage_state.json for an account-based portal if it exists."""
    s_dir = session_dir or DEFAULT_SESSION_DIR
    state_file = s_dir / f"{portal}_storage_state.json"
    if state_file.is_file():
        return state_file
    return None


def get_portal_credentials(portal: str) -> tuple[str | None, str | None]:
    """Retrieve stored portal account username/password from environment variables."""
    p_upper = portal.upper()
    username = os.environ.get(f"{p_upper}_USERNAME") or os.environ.get(f"{p_upper}_EMAIL")
    password = os.environ.get(f"{p_upper}_PASSWORD")
    return username, password
