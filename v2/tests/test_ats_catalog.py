"""Unit tests for capability-specific ATS registry and manifest."""
from __future__ import annotations

import pytest

from app.ats_catalog import (
    ATS_REGISTRY,
    CapabilityStatus,
    DifficultyTier,
    get_full_ats_manifest,
    get_platform_capabilities,
)


def test_capability_specific_certification_separation():
    # 1. Ashby is live certified
    ashby = get_platform_capabilities("ashby")
    assert ashby is not None
    assert ashby.is_live_certified is True
    assert ashby.capabilities.live_submission is CapabilityStatus.LIVE_SUBMIT_CERTIFIED
    assert ashby.difficulty_tier is DifficultyTier.LOW

    # 2. SmartRecruiters is dry-run certified, NOT live certified
    sr = get_platform_capabilities("smartrecruiters")
    assert sr is not None
    assert sr.is_live_certified is False
    assert sr.is_dry_run_certified is True
    assert sr.capabilities.live_submission is CapabilityStatus.IMPLEMENTED
    assert "DataDome" in sr.known_limitations[0]

    # 3. Workday is dry-run certified, NOT live certified
    workday = get_platform_capabilities("workday")
    assert workday is not None
    assert workday.is_live_certified is False
    assert workday.is_dry_run_certified is True
    assert workday.capabilities.live_submission is CapabilityStatus.IMPLEMENTED
    assert workday.difficulty_tier is DifficultyTier.HIGH

    # 4. Unknown platform returns None
    assert get_platform_capabilities("non_existent_ats") is None


def test_get_full_ats_manifest_structure():
    manifest = get_full_ats_manifest()
    assert manifest["framework_version"] == "hunterx-ats-catalog-v2"
    assert "head_commit" in manifest
    assert "ashby" in manifest["live_certified_platforms"]
    assert "smartrecruiters" not in manifest["live_certified_platforms"]
    assert "workday" not in manifest["live_certified_platforms"]
    assert "ashby" in manifest["dry_run_certified_platforms"]
    assert "greenhouse" in manifest["dry_run_certified_platforms"]
    assert "lever" in manifest["dry_run_certified_platforms"]
