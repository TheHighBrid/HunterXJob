"""Capability-Specific ATS Registry and Certification Manifest.

Maintains an immutable, capability-specific registry for all supported ATS platforms:
- Separates certification by granular capability:
  1. Discovery & Metadata Extraction
  2. Form Control Parsing & Schema Mapping
  3. Interactive DOM Form Filling & Autocomplete
  4. File Attachment & Upload
  5. Dry-Run DOM Read-Back Verification
  6. Live Submission & Receipt Capture
- Distinguishes capability certification from runtime execution authorization.
- Records known limitations, CAPTCHA requirements, and test references.
- Binds certification to exact framework versions and git commits.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from app.certification_system import get_git_commit

logger = logging.getLogger(__name__)


class CapabilityStatus(str, Enum):
    NOT_IMPLEMENTED = "not_implemented"
    IMPLEMENTED = "implemented"
    TESTED = "tested"
    DRY_RUN_CERTIFIED = "dry_run_certified"
    LIVE_SUBMIT_CERTIFIED = "live_submit_certified"


class DifficultyTier(str, Enum):
    LOW = "low"        # Lever, Greenhouse, Workable, Breezy, Recruitee
    MEDIUM = "medium"  # SmartRecruiters, BambooHR, Teamtailor, Rippling, Pinpoint, JazzHR
    HIGH = "high"      # Workday, iCIMS, Oracle Recruiting Cloud
    GOV_PORTAL = "gov" # Canadian Federal, Provincial, Municipal


@dataclass(frozen=True)
class ATSCapabilities:
    discovery: CapabilityStatus
    form_parsing: CapabilityStatus
    in_page_filling: CapabilityStatus
    file_upload: CapabilityStatus
    dry_run_verification: CapabilityStatus
    live_submission: CapabilityStatus


@dataclass(frozen=True)
class ATSPlatformEntry:
    platform_id: str
    display_name: str
    difficulty_tier: DifficultyTier
    capabilities: ATSCapabilities
    supported_field_types: tuple[str, ...]
    dynamic_field_handling: str
    captcha_boundary: str
    auth_requirement: str
    known_limitations: tuple[str, ...]
    test_references: tuple[str, ...]
    last_verified_commit: str
    last_verified_date: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["difficulty_tier"] = self.difficulty_tier.value
        data["capabilities"] = {k: v.value for k, v in asdict(self.capabilities).items()}
        return data

    @property
    def is_live_certified(self) -> bool:
        return self.capabilities.live_submission is CapabilityStatus.LIVE_SUBMIT_CERTIFIED

    @property
    def is_dry_run_certified(self) -> bool:
        return self.capabilities.dry_run_verification in {
            CapabilityStatus.DRY_RUN_CERTIFIED,
            CapabilityStatus.LIVE_SUBMIT_CERTIFIED,
        }


# Canonical capability registry
ATS_REGISTRY: dict[str, ATSPlatformEntry] = {
    "ashby": ATSPlatformEntry(
        platform_id="ashby",
        display_name="Ashby HQ",
        difficulty_tier=DifficultyTier.LOW,
        capabilities=ATSCapabilities(
            discovery=CapabilityStatus.LIVE_SUBMIT_CERTIFIED,
            form_parsing=CapabilityStatus.LIVE_SUBMIT_CERTIFIED,
            in_page_filling=CapabilityStatus.LIVE_SUBMIT_CERTIFIED,
            file_upload=CapabilityStatus.LIVE_SUBMIT_CERTIFIED,
            dry_run_verification=CapabilityStatus.LIVE_SUBMIT_CERTIFIED,
            live_submission=CapabilityStatus.LIVE_SUBMIT_CERTIFIED,
        ),
        supported_field_types=("text", "textarea", "select", "multiselect", "radio", "checkbox", "file", "location"),
        dynamic_field_handling="GraphQL schema + data-field-path container resolution",
        captcha_boundary="Invisible reCAPTCHA v3 or interactive puzzle post-submit",
        auth_requirement="None (Public application form)",
        known_limitations=("Heavy JavaScript autocomplete requires country-aware option scoring",),
        test_references=("v2/tests/test_ats_forms.py", "v2/tests/test_browser_filler.py"),
        last_verified_commit="6b9a855",
        last_verified_date="2026-10-28",
    ),
    "greenhouse": ATSPlatformEntry(
        platform_id="greenhouse",
        display_name="Greenhouse",
        difficulty_tier=DifficultyTier.LOW,
        capabilities=ATSCapabilities(
            discovery=CapabilityStatus.DRY_RUN_CERTIFIED,
            form_parsing=CapabilityStatus.DRY_RUN_CERTIFIED,
            in_page_filling=CapabilityStatus.DRY_RUN_CERTIFIED,
            file_upload=CapabilityStatus.DRY_RUN_CERTIFIED,
            dry_run_verification=CapabilityStatus.DRY_RUN_CERTIFIED,
            live_submission=CapabilityStatus.DRY_RUN_CERTIFIED,
        ),
        supported_field_types=("text", "textarea", "select", "radio", "checkbox", "file", "location"),
        dynamic_field_handling="Public Boards API GET + React ARIA controls",
        captcha_boundary="hCaptcha or Cloudflare Turnstile on submit",
        auth_requirement="None (Public job board)",
        known_limitations=("Dynamic question IDs with brackets require attribute selector escaping",),
        test_references=("v2/tests/test_greenhouse_form.py", "v2/scripts/gate2.py"),
        last_verified_commit="6b9a855",
        last_verified_date="2026-10-28",
    ),
    "lever": ATSPlatformEntry(
        platform_id="lever",
        display_name="Lever",
        difficulty_tier=DifficultyTier.LOW,
        capabilities=ATSCapabilities(
            discovery=CapabilityStatus.DRY_RUN_CERTIFIED,
            form_parsing=CapabilityStatus.DRY_RUN_CERTIFIED,
            in_page_filling=CapabilityStatus.DRY_RUN_CERTIFIED,
            file_upload=CapabilityStatus.DRY_RUN_CERTIFIED,
            dry_run_verification=CapabilityStatus.DRY_RUN_CERTIFIED,
            live_submission=CapabilityStatus.DRY_RUN_CERTIFIED,
        ),
        supported_field_types=("text", "textarea", "select", "radio", "checkbox", "file"),
        dynamic_field_handling="Static HTML apply form + card question blocks",
        captcha_boundary="hCaptcha iframe challenge on submit",
        auth_requirement="None (Public job posting)",
        known_limitations=("Cards vary between company custom postings",),
        test_references=("v2/tests/test_ats_forms.py", "v2/tests/test_browser_filler.py"),
        last_verified_commit="6b9a855",
        last_verified_date="2026-10-28",
    ),
    "smartrecruiters": ATSPlatformEntry(
        platform_id="smartrecruiters",
        display_name="SmartRecruiters",
        difficulty_tier=DifficultyTier.MEDIUM,
        capabilities=ATSCapabilities(
            discovery=CapabilityStatus.DRY_RUN_CERTIFIED,
            form_parsing=CapabilityStatus.DRY_RUN_CERTIFIED,
            in_page_filling=CapabilityStatus.DRY_RUN_CERTIFIED,
            file_upload=CapabilityStatus.DRY_RUN_CERTIFIED,
            dry_run_verification=CapabilityStatus.DRY_RUN_CERTIFIED,
            live_submission=CapabilityStatus.IMPLEMENTED,  # Pre-form DataDome boundary; not live certified
        ),
        supported_field_types=("text", "textarea", "select", "radio", "checkbox", "file"),
        dynamic_field_handling="JobRef JSON schema + in-page form controls",
        captcha_boundary="DataDome anti-bot challenge on pre-form navigation",
        auth_requirement="SmartToken or SmartProfile account",
        known_limitations=("Pre-form DataDome bot detection blocks unheaded automation",),
        test_references=("v2/tests/test_smartrecruiters_and_workday.py",),
        last_verified_commit="6b9a855",
        last_verified_date="2026-10-28",
    ),
    "workday": ATSPlatformEntry(
        platform_id="workday",
        display_name="Workday",
        difficulty_tier=DifficultyTier.HIGH,
        capabilities=ATSCapabilities(
            discovery=CapabilityStatus.DRY_RUN_CERTIFIED,
            form_parsing=CapabilityStatus.DRY_RUN_CERTIFIED,
            in_page_filling=CapabilityStatus.DRY_RUN_CERTIFIED,
            file_upload=CapabilityStatus.DRY_RUN_CERTIFIED,
            dry_run_verification=CapabilityStatus.DRY_RUN_CERTIFIED,
            live_submission=CapabilityStatus.IMPLEMENTED,  # Account auth required; not live certified
        ),
        supported_field_types=("text", "textarea", "select", "radio", "checkbox", "file"),
        dynamic_field_handling="data-automation-id attribute mapping + multi-step wizard",
        captcha_boundary="Account creation CAPTCHA and email verification",
        auth_requirement="Mandatory applicant account creation per tenant",
        known_limitations=("Multi-step wizard and tenant login requirements",),
        test_references=("v2/tests/test_smartrecruiters_and_workday.py",),
        last_verified_commit="6b9a855",
        last_verified_date="2026-10-28",
    ),
    "canadian_gov": ATSPlatformEntry(
        platform_id="canadian_gov",
        display_name="Canadian Public Sector Portals",
        difficulty_tier=DifficultyTier.GOV_PORTAL,
        capabilities=ATSCapabilities(
            discovery=CapabilityStatus.DRY_RUN_CERTIFIED,
            form_parsing=CapabilityStatus.DRY_RUN_CERTIFIED,
            in_page_filling=CapabilityStatus.DRY_RUN_CERTIFIED,
            file_upload=CapabilityStatus.DRY_RUN_CERTIFIED,
            dry_run_verification=CapabilityStatus.DRY_RUN_CERTIFIED,
            live_submission=CapabilityStatus.IMPLEMENTED,  # Supervised dry-run certified
        ),
        supported_field_types=("text", "textarea", "select", "radio", "checkbox", "file"),
        dynamic_field_handling="Bilingual EN/FR screening + SLE language profiles + citizenship preference",
        captcha_boundary="Government login portals (GCKey / Public Service accounts)",
        auth_requirement="Government applicant session storage (PSEA citizenship priority)",
        known_limitations=("Strict security clearance and bilingual profiling constraints",),
        test_references=("v2/tests/test_canadian_gov_portals.py", "v2/tests/test_dryrun_new_units.py"),
        last_verified_commit="fc6bf9a",
        last_verified_date="2026-10-28",
    ),
}


def get_platform_capabilities(platform_id: str) -> ATSPlatformEntry | None:
    """Retrieve capability-specific entry for an ATS platform."""
    return ATS_REGISTRY.get(platform_id.lower())


def get_full_ats_manifest() -> dict[str, Any]:
    """Generate canonical ATS capability manifest tied to HEAD commit."""
    commit = get_git_commit()
    return {
        "framework_version": "hunterx-ats-catalog-v2",
        "head_commit": commit,
        "platforms": {k: v.as_dict() for k, v in ATS_REGISTRY.items()},
        "live_certified_platforms": [k for k, v in ATS_REGISTRY.items() if v.is_live_certified],
        "dry_run_certified_platforms": [k for k, v in ATS_REGISTRY.items() if v.is_dry_run_certified],
    }
