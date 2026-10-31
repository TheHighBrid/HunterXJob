"""Challenger Candidate Synthesizer.

Reads open DefectClusters and synthesizes targeted declarative patches to the
AgentHarness, creating a new challenger AgentVersion for evaluation in the sandbox.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from improvement_kernel.clustering.taxonomy import FailureCategory
from improvement_kernel.optimizer.harness import AgentHarness, default_harness
from improvement_kernel.storage.db import get_current_champion
from improvement_kernel.storage.models import (
    AgentVersion,
    DefectCluster,
    DefectStatus,
    VersionStatus,
)

logger = logging.getLogger("improvement_kernel.optimizer.synthesizer")


class CandidateSynthesizer:
    """Synthesizes candidate AgentVersion harnesses to address active defect clusters."""

    def __init__(self, session: Session, project: str = "hunterxjob") -> None:
        self.session = session
        self.project = project

    def synthesize_for_cluster(self, cluster: DefectCluster) -> AgentVersion | None:
        """Synthesize a targeted challenger version for a specific defect cluster."""
        champion = get_current_champion(self.session, self.project)
        champion_harness = AgentHarness.from_dict(champion.harness_config)

        patch = self._generate_patch_for_defect(cluster)
        if not patch:
            logger.info("No automatic declarative patch could be synthesized for cluster %s", cluster.id)
            return None

        challenger_harness = champion_harness.apply_patch(patch)

        # Generate version identifier with microsecond and cluster id for uniqueness
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        cid_slug = (cluster.id or "")[:6]
        candidate_version = f"{champion.version.split('-')[0]}-challenger-{cluster.failure_code.lower()}-{cid_slug}-{timestamp}"

        candidate = AgentVersion(
            project=self.project,
            version=candidate_version,
            git_commit=champion.git_commit,
            prompt_hash=challenger_harness.prompt_hash,
            config_hash=challenger_harness.config_hash,
            harness_config_json=json.dumps(challenger_harness.to_dict(), sort_keys=True),
            status=VersionStatus.candidate.value,
            risk_level="low",
            notes=(
                f"Challenger synthesized to fix defect {cluster.pattern_signature} "
                f"({cluster.failure_category}/{cluster.failure_code})"
            ),
        )
        self.session.add(candidate)

        # Update defect cluster
        cluster.status = DefectStatus.synthesized.value
        cluster.proposed_fix_summary = (
            f"Synthesized version {candidate_version}. Patch: {json.dumps(patch, sort_keys=True)}"
        )
        self.session.commit()

        logger.info("Successfully synthesized challenger %s for defect %s", candidate_version, cluster.id)
        return candidate

    def synthesize_all_open(self) -> list[AgentVersion]:
        """Find all open defect clusters and synthesize challengers for them."""
        stmt = (
            select(DefectCluster)
            .where(DefectCluster.project == self.project, DefectCluster.status == DefectStatus.open.value)
            .order_by(DefectCluster.occurrences_count.desc())
        )
        clusters = list(self.session.execute(stmt).scalars().all())
        candidates: list[AgentVersion] = []

        for cluster in clusters:
            candidate = self.synthesize_for_cluster(cluster)
            if candidate:
                candidates.append(candidate)

        return candidates

    def _generate_patch_for_defect(self, cluster: DefectCluster) -> dict[str, Any]:
        """Derive declarative patch based on failure taxonomy and pattern signature."""
        if getattr(cluster, "project", "") == "fidelis" or self.project == "fidelis":
            return self._generate_fidelis_routing_patch(cluster)
        if getattr(cluster, "project", "") == "pedit" or self.project == "pedit":
            return self._generate_pedit_tuning_patch(cluster)

        category = cluster.failure_category
        code = cluster.failure_code
        sig = cluster.pattern_signature.lower()

        patch: dict[str, Any] = {}

        if category == FailureCategory.SAFETY:
            if any(k in (code + " " + sig).lower() for k in ("truth_guard", "unsupported", "qualification", "credential", "cpa", "cfa", "clearance")):
                patch["answer_policies"] = {
                    "truth_guard_strict": True,
                    "require_verified_credential": True,
                    "unverified_credential_action": "refuse_or_no",
                }
                patch["prompt_templates"] = {
                    "credential_guard": (
                        "CRITICAL: If asked about any professional certification, license, or designation "
                        "(e.g. CPA, CFA, PMP, Security Clearance), verify candidate profile. "
                        "If missing, strictly output 'No' or select the negative option."
                    )
                }

        elif any(k in (code + " " + sig).lower() for k in ("location", "country", "mismatch", "_systemfield_location")):
            patch["answer_policies"] = {
                "location_country_enforcement": "CA",
                "reject_foreign_matches": True,
                "allowed_country_codes": ["CA", "CAN"],
                "preferred_cities": ["Ottawa", "Gatineau"],
            }
            patch["prompt_templates"] = {
                "location_selector": (
                    "When selecting from location autocomplete dropdowns, verify that the country is Canada (CA). "
                    "Never select US locations (such as Ontario, California)."
                )
            }
            patch["selectors"] = {
                "ashby": {
                    "location": [
                        "input[id*='_systemfield_location']",
                        "input[name*='_systemfield_location']",
                        "input[data-qa*='_systemfield_location']",
                        "[data-qa='location-picker'] input",
                        "input[placeholder*='Location' i]",
                    ]
                }
            }

        elif "resume" in sig or "attach" in sig or "cv" in sig:
            patch["selectors"] = {
                "global": {
                    "resume": [
                        "input[type='file'][name*='resume' i]",
                        "input[type='file'][name*='cv' i]",
                        "input[type='file'][id*='resume' i]",
                        "input[type='file'][data-qa*='resume' i]",
                        "input[type='file'][aria-label*='resume' i]",
                        "input[type='file'][aria-label*='cv' i]",
                        "input[type='file']",
                    ]
                }
            }

        elif "first_name" in sig or "last_name" in sig or "name" in sig:
            patch["selectors"] = {
                "global": {
                    "first_name": [
                        "input[name*='firstName' i]",
                        "input[name*='first_name' i]",
                        "input[id*='first_name' i]",
                        "input[aria-label*='first name' i]",
                        "input[placeholder*='first name' i]",
                    ]
                }
            }

        elif "outside_location_policy" in sig or "prescreen" in code.lower():
            patch["answer_policies"] = {
                "remote_only_for_other_regions": True,
                "default_allowed_locations": ["Ottawa", "Gatineau", "Remote", "Ontario, Canada"],
            }

        elif "invoke_agent" in sig or "timeout" in sig or code == "TOOL_GENERIC_ERROR":
            patch["tool_routing"] = {
                "timeout_seconds": 45,
                "max_retries": 3,
                "backoff_factor": 1.5,
            }

        elif "unmatched" in sig or "selector" in code.lower():
            # Heuristic fallback selector expansion
            target_field = "custom_field"
            for field in ["resume", "phone", "email", "name", "location", "linkedin", "portfolio"]:
                if field in sig:
                    target_field = field
                    break

            patch["selectors"] = {
                "global": {
                    target_field: [
                        f"input[name*='{target_field}' i]",
                        f"input[id*='{target_field}' i]",
                        f"[data-qa*='{target_field}' i]",
                        f"[aria-label*='{target_field}' i]",
                    ]
                }
            }
        else:
            patch["tool_routing"] = {
                "fallback_enabled": True,
                "retry_on_error": True,
            }

        return patch

    def _generate_fidelis_routing_patch(self, cluster: DefectCluster) -> dict[str, Any]:
        """Generate specialized model routing rules and tile sizes for Fidelis image restoration defects."""
        code = (cluster.failure_code or "").upper()
        sig = (cluster.pattern_signature or "").lower()

        patch: dict[str, Any] = {}

        if "PLASTIC_SKIN" in code or "portrait" in sig:
            # Low-light portraits fail on default profile -> route to gfpgan_face with tile_size 256 & anti-dollification
            patch["routing_rules"] = {
                "low_light_portrait": {
                    "model_profile": "gfpgan_face",
                    "tile_size": 256,
                    "overlap": 32,
                    "face_fidelity_w": 0.65,
                    "color_fix": "wavelet",
                    "anti_dollification": True,
                }
            }
            patch["tile_config"] = {
                "portrait_tile_size": 256,
                "low_light_tile_size": 256,
            }
        elif "MUTATED_TEXT" in code or "RINGING" in code or "text" in sig:
            # Text graphics / typography fail on default profile -> route to d2fk-v1 with tile_size 512 & anti-ringing
            patch["routing_rules"] = {
                "text_graphics": {
                    "model_profile": "d2fk-v1",
                    "tile_size": 512,
                    "overlap": 48,
                    "text_preserve": True,
                    "text_unsharp_strength": 0.55,
                    "anti_ringing_filter": True,
                }
            }
            patch["tile_config"] = {
                "text_tile_size": 512,
                "text_overlap": 48,
            }
        elif "VRAM" in code or "BLACK_TILE" in code or "tile" in sig:
            # VRAM exhaustion or black tiles -> downscale tile sizes
            patch["tile_config"] = {
                "default_tile_size": 256,
                "overlap": 32,
                "vram_safe_mode": True,
            }
        elif "COLOR" in code:
            patch["routing_rules"] = {
                "global": {
                    "color_fix": "wavelet",
                    "wavelet_levels": 5,
                }
            }
        else:
            patch["routing_rules"] = {
                "general": {
                    "model_profile": "Smart_Balanced",
                    "tile_size": 512,
                }
            }

        return patch

    def _generate_pedit_tuning_patch(self, cluster: DefectCluster) -> dict[str, Any]:
        """Generate auto-tuned font substitution heuristics and word-wrapping algorithms for Pedit PDF edits."""
        code = (cluster.failure_code or "").upper()
        sig = (cluster.pattern_signature or "").lower()

        patch: dict[str, Any] = {}

        if any(k in (code + " " + sig) for k in ("BOUNDING_BOX_OVERFLOW", "OVERFLOW", "EXPANSION", "WIDTH", "COLLIDE")):
            # Text expanded beyond original bounding box -> auto-tune word-wrapping and font step-down
            patch["layout_fallbacks"] = {
                "enable_word_wrap": True,
                "enable_font_step_down": True,
                "enable_kerning_fallback": True,
                "max_bbox_overflow_pt": 0.5,
                "min_font_scale_ratio": 0.80,
                "tracking_compression_limit_pt": -0.45,
            }
            patch["word_wrapping_algorithms"] = {
                "line_wrap_mode": "whitespace_boundary",
                "hyphenation_fallback": True,
                "line_spacing_ratio": 1.18,
            }
        elif any(k in (code + " " + sig) for k in ("FONT_MISMATCH", "FONT", "SUBSET", "GLYPH", "MISSING_GLYPH")):
            # Missing glyphs or unextractable font -> auto-tune font substitution and glyph synthesis
            patch["font_substitution_heuristics"] = {
                "enable_metric_fallback": True,
                "enable_type1_glyph_synthesis": True,
                "strip_postscript_mt_suffix": True,
                "max_font_match_distance": 0.15,
                "metric_fallback_map": {
                    "arial": "helv",
                    "timesnewroman": "tiro",
                    "couriernew": "cour",
                },
            }
        elif any(k in (code + " " + sig) for k in ("KERNING", "TRACKING", "LAYOUT_DRIFT", "BASELINE")):
            # Variable kerning or baseline drift -> auto-tune kerning fallback and tracking compression
            patch["layout_fallbacks"] = {
                "enable_kerning_fallback": True,
                "tracking_compression_limit_pt": -0.35,
                "enforce_zero_baseline_drift": True,
                "max_bbox_overflow_pt": 0.5,
            }
        else:
            patch["layout_fallbacks"] = {
                "enable_metric_fallback": True,
                "enable_kerning_fallback": True,
                "enable_word_wrap": True,
                "enable_font_step_down": True,
                "max_bbox_overflow_pt": 0.5,
            }

        return patch
