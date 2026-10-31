"""Declarative Agent Harness representation and serialization.

The agent harness encapsulates all declarative configuration governing the agent's behavior:
- selectors: Platform-specific and global DOM selectors
- answer_policies: Verification invariants and screening answer policies
- prompt_templates: Versioned system/task prompts
- decision_weights: Match scoring thresholds, radius/commute constraints, risk limits
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AgentHarness:
    selectors: dict[str, Any] = field(default_factory=dict)
    answer_policies: dict[str, Any] = field(default_factory=dict)
    prompt_templates: dict[str, str] = field(default_factory=dict)
    decision_weights: dict[str, float] = field(default_factory=dict)
    routing_rules: dict[str, Any] = field(default_factory=dict)
    tile_config: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "selectors": copy.deepcopy(self.selectors),
            "answer_policies": copy.deepcopy(self.answer_policies),
            "prompt_templates": copy.deepcopy(self.prompt_templates),
            "decision_weights": copy.deepcopy(self.decision_weights),
        }
        if self.routing_rules:
            d["routing_rules"] = copy.deepcopy(self.routing_rules)
        if self.tile_config:
            d["tile_config"] = copy.deepcopy(self.tile_config)
        d.update(copy.deepcopy(self.extra))
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentHarness:
        known_keys = {"selectors", "answer_policies", "prompt_templates", "decision_weights", "routing_rules", "tile_config"}
        extra = {k: copy.deepcopy(v) for k, v in data.items() if k not in known_keys}
        return cls(
            selectors=copy.deepcopy(data.get("selectors", {})),
            answer_policies=copy.deepcopy(data.get("answer_policies", {})),
            prompt_templates=copy.deepcopy(data.get("prompt_templates", {})),
            decision_weights=copy.deepcopy(data.get("decision_weights", {})),
            routing_rules=copy.deepcopy(data.get("routing_rules", {})),
            tile_config=copy.deepcopy(data.get("tile_config", {})),
            extra=extra,
        )

    @property
    def prompt_hash(self) -> str:
        serialized = json.dumps(self.prompt_templates, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    @property
    def config_hash(self) -> str:
        serialized = json.dumps(
            {
                "selectors": self.selectors,
                "answer_policies": self.answer_policies,
                "decision_weights": self.decision_weights,
            },
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def diff(self, other: AgentHarness) -> dict[str, Any]:
        """Compute the structural diff between this harness and another harness."""
        self_dict = self.to_dict()
        other_dict = other.to_dict()
        delta: dict[str, Any] = {}

        for section in ["selectors", "answer_policies", "prompt_templates", "decision_weights"]:
            s_val = self_dict.get(section, {})
            o_val = other_dict.get(section, {})
            if s_val != o_val:
                delta[section] = {
                    "from": s_val,
                    "to": o_val,
                }
        return delta

    def apply_patch(self, patch: dict[str, Any]) -> AgentHarness:
        """Return a new AgentHarness with patch applied recursively."""
        new_dict = copy.deepcopy(self.to_dict())

        for section, section_patch in patch.items():
            if section not in new_dict:
                new_dict[section] = copy.deepcopy(section_patch)
                continue

            if isinstance(section_patch, dict) and isinstance(new_dict[section], dict):
                # Deep merge or update
                for k, v in section_patch.items():
                    if isinstance(v, list) and isinstance(new_dict[section].get(k), list):
                        # Combine or replace depending on patch directive
                        # If list of selectors, prepend/merge unique items
                        existing = new_dict[section][k]
                        for item in v:
                            if item not in existing:
                                existing.insert(0, item)
                    else:
                        new_dict[section][k] = copy.deepcopy(v)
            else:
                new_dict[section] = copy.deepcopy(section_patch)

        return AgentHarness.from_dict(new_dict)


def default_harness() -> AgentHarness:
    """Generate the baseline production harness for HunterXJob."""
    return AgentHarness(
        selectors={
            "global": {
                "name": ["input[name='name']", "#name", "input[data-qa*='name']", "input[data-ui*='name']"],
                "email": ["input[type='email']", "#email", "input[name='email']"],
                "phone": ["input[type='tel']", "#phone", "input[name='phone']"],
                "resume": [
                    "input[type='file'][id*='resume' i]",
                    "input[type='file'][name*='resume' i]",
                    "input[type='file']",
                ],
                "location": [
                    "input[name='location']",
                    "#location",
                    "[data-qa*='location']",
                    "input.location-input",
                    "input[placeholder*='Location' i]",
                ],
                "submit": [
                    "button[type='submit']",
                    "button:has-text('Submit Application')",
                    "button:has-text('Submit')",
                    "button:has-text('Apply')",
                ],
            },
            "ashby": {
                "location": [
                    "input[id*='_systemfield_location']",
                    "input[name*='_systemfield_location']",
                    "input[placeholder*='Location']",
                    "div[data-qa='location-picker'] input",
                ],
                "resume": ["input[type='file'][id*='resume']", "input[type='file']"],
            },
            "greenhouse": {
                "location": ["#job_application_location", "input#location", "input[name*='location']"],
                "resume": ["#resume", "input[data-field='resume']", "input[type='file']"],
            },
            "lever": {
                "location": ["input[name='location']", "input[data-qa='location']"],
                "resume": ["input[type='file'][name='resume']", "input[type='file']"],
            },
            "workable": {
                "location": ["input[name='location']", "#location"],
                "resume": ["input[data-ui='resume']", "input[type='file']"],
            },
        },
        answer_policies={
            "truth_guard_strict": True,
            "require_verified_credential": True,
            "location_country_enforcement": "CA",
            "default_allowed_locations": ["Ottawa", "Gatineau", "Remote", "Ontario, Canada"],
            "remote_only_for_other_regions": True,
            "unverified_credential_action": "refuse_or_no",
            "security_clearance_default": "I do not have security clearance",
            "blacklisted_companies": ["wealthsimple", "1password", "cohere"],
        },
        prompt_templates={
            "form_filler": (
                "You are the HunterXJob application assistant. You answer form questions "
                "truthfully using ONLY facts verified in the candidate's profile. "
                "If a credential (such as CPA, PMP, Top Secret Clearance) is requested and not verified "
                "in the profile, you MUST select 'No' or refuse. Do not guess."
            ),
            "location_matcher": (
                "Ensure selected location is in Canada (preferably Ottawa-Gatineau or Remote). "
                "Reject matches for US states (e.g. Ontario, CA / Ontario, California) when Canada is expected."
            ),
        },
        decision_weights={
            "min_score_threshold": 65.0,
            "max_commute_km": 40.0,
            "canadian_experience_bonus": 10.0,
            "truth_guard_weight": 1.0,
        },
    )
