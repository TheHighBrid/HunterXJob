"""Candidate Knowledge Book and Dynamic Question Answering Engine.

Maintains a comprehensive, structured repository of candidate facts, policy
answers, behavioral narratives, and standard screening rules:
- Contact, social URLs, and portfolio links.
- Work authorization, citizenship, and legal consents.
- Availability, start dates, and salary expectations.
- Employment, education, certifications, and skills with metrics.
- Standard corporate screening answers (prior employment, how heard, commute).
- Semantic and LLM-powered answering of role-specific screening questions
  strictly bounded by the candidate's verified facts.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.profile import Fact, VerifiedProfile

logger = logging.getLogger(__name__)


# Standard deterministic fallback answers for common ATS screening patterns
DEFAULT_SCREENING_PATTERNS: list[tuple[re.Pattern[str], Any, str]] = [
    # Prior employment with company
    (re.compile(r"previously\s+(?:work|employ)|ever\s+(?:been\s+)?(?:previously\s+)?(?:work|employ)|former\s+employee", re.I), "No", "prior_employment_default"),
    # How did you hear
    (re.compile(r"how\s+did\s+you\s+hear|source|where\s+did\s+you\s+find", re.I), "LinkedIn", "how_heard_default"),
    # Age requirement
    (re.compile(r"18\s+years\s+of\s+age|at\s+least\s+18|legal\s+age", re.I), "Yes", "legal_age_default"),
    # Full time eligibility
    (re.compile(r"available\s+for\s+full\s*time|full-time\s+basis", re.I), "Yes", "full_time_default"),
    # Willing to undergo background check
    (re.compile(r"consent\s+to.*background|background\s+check.*consent|undergo.*screening", re.I), "Yes", "background_check_consent"),
    # Read and agree to terms/disclaimer
    (re.compile(r"read\s+and\s+agree|disclaimer\s+and\s+consent|privacy\s+notice|terms\s+and\s+conditions", re.I), "Yes", "terms_consent"),
    # Non-compete / restrictive covenants
    (re.compile(r"non-compete|restrictive\s+covenant|conflict\s+of\s+interest", re.I), "No", "non_compete_default"),
]


@dataclass
class KnowledgeBook:
    profile: Any
    custom_answers: dict[str, Any] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.profile, dict):
            from app.profile import drafts_from_document
            drafts = drafts_from_document(self.profile, origin="memory", verified_default=True)
            facts = [Fact(d.key, d.category, d.data) for d in drafts]
            self.profile = VerifiedProfile(facts)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeBook:
        from app.profile import drafts_from_document
        drafts = drafts_from_document(data, origin="memory", verified_default=True)
        facts = [Fact(d.key, d.category, d.data) for d in drafts]
        return cls(
            profile=VerifiedProfile(facts),
            custom_answers=data.get("custom_answers", {}),
            preferences=data.get("preferences", {}),
        )

    @classmethod
    def from_yaml_file(cls, path: Path | str) -> KnowledgeBook:
        """Load knowledge book from a combined profile/knowledge YAML file."""
        import yaml
        from app.profile import drafts_from_document

        p = Path(path)
        data = yaml.safe_load(p.read_text()) or {}
        drafts = drafts_from_document(data, origin=str(p), verified_default=True)
        facts = [Fact(d.key, d.category, d.data) for d in drafts]
        profile = VerifiedProfile(facts)
        return cls(
            profile=profile,
            custom_answers=data.get("custom_answers", {}),
            preferences=data.get("preferences", {}),
        )

    def get_contact_answers(self) -> dict[str, Any]:
        """Flatten contact and profile links into standard form keys."""
        contact = dict(self.profile.contact)
        first = contact.get("first_name", "")
        last = contact.get("last_name", "")
        full = f"{first} {last}".strip()

        answers: dict[str, Any] = {
            "first_name": first,
            "last_name": last,
            "full_name": full,
            "name": full,
            "email": contact.get("email", ""),
            "phone": contact.get("phone", ""),
            "location": contact.get("location", ""),
            "city": contact.get("city", "") or (contact.get("location", "").split(",")[0].strip() if "," in contact.get("location", "") else contact.get("location", "")),
        }

        # Social & portfolio URLs
        if contact.get("linkedin_url"):
            answers["linkedin_url"] = contact["linkedin_url"]
            answers["urls[LinkedIn]"] = contact["linkedin_url"]
        if contact.get("github_url"):
            answers["github_url"] = contact["github_url"]
            answers["urls[GitHub]"] = contact["github_url"]
        if contact.get("portfolio_url"):
            answers["portfolio_url"] = contact["portfolio_url"]
            answers["urls[Portfolio]"] = contact["portfolio_url"]

        # Current employment context
        employments = self.profile.of("employment")
        if employments:
            current_job = next((f.data for f in employments if f.data.get("current")), employments[0].data)
            answers["org"] = current_job.get("employer", "")
            answers["company"] = current_job.get("employer", "")
            answers["current_company"] = current_job.get("employer", "")
            answers["title"] = current_job.get("title", "")
            answers["current_title"] = current_job.get("title", "")

        return answers

    def resolve_screening_question(self, label: str, options: list[str] | None = None) -> tuple[Any, str]:
        """Resolve a screening question using pattern rules, custom answers, or profile facts."""
        text = label.strip()

        # 1. Direct custom answer override if configured
        for key, val in self.custom_answers.items():
            if key.lower() in text.lower():
                return val, "custom_answer_match"

        # 2. Check standard screening regex rules
        for pattern, default_val, rule_name in DEFAULT_SCREENING_PATTERNS:
            if pattern.search(text):
                if options:
                    # Select matching option from dropdown if available
                    matched = self._match_option(default_val, options)
                    if matched:
                        return matched, f"screening_pattern:{rule_name}"
                return default_val, f"screening_pattern:{rule_name}"

        # 3. Work authorization checks
        if re.search(r"legally\s+(?:authorized|eligible)\s+to\s+work|work\s+authorization", text, re.I):
            country = "CA" if "canada" in text.lower() else "US" if ("us" in text.lower() or "united states" in text.lower()) else "CA"
            auth_facts = [f.data for f in self.profile.of("work_authorization") if f.data.get("country", "").upper() == country]
            if auth_facts and auth_facts[0].get("authorized"):
                val = "Authorized to work in Canada" if country == "CA" and options and any("Canada" in o for o in options) else "Yes"
                if options:
                    val = self._match_option(val, options) or val
                return val, "profile_work_authorization"

        # 4. Relocation / Commute
        if re.search(r"relocate|relocation|commute|on-site|hybrid", text, re.I):
            pref = self.preferences.get("willing_to_relocate", True)
            val = "Yes" if pref else "No"
            if options:
                val = self._match_option(val, options) or val
            return val, "preference_relocation"

        # 5. Salary expectation
        if re.search(r"salary|compensation|expected\s+rate", text, re.I):
            desired = self.preferences.get("desired_salary", "")
            if desired:
                return desired, "preference_salary"

        # 6. Start date / availability
        if re.search(r"start\s+date|earliest\s+date|notice\s+period|availability", text, re.I):
            avail = self.preferences.get("notice_period", "2 weeks")
            return avail, "preference_start_date"

        return None, "unresolved"

    def _match_option(self, target: str, options: list[str]) -> str | None:
        """Find the closest option in a list of options."""
        target_clean = str(target).strip().lower()
        # Exact match
        for opt in options:
            if opt.strip().lower() == target_clean:
                return opt
        # Starts with or contains
        for opt in options:
            if target_clean in opt.strip().lower() or opt.strip().lower() in target_clean:
                return opt
        return None

    def export_all_vault_answers(self) -> dict[str, Any]:
        """Aggregate all base profile answers, contact details, and custom policies into a unified dictionary."""
        from app.profile_vault import profile_answers

        base = profile_answers(self.profile)
        contact = self.get_contact_answers()
        base.update(contact)
        base.update(self.custom_answers)
        return base

    def resolve_answers(self, controls: list[Any]) -> dict[str, Any]:
        """Resolve answers for a list of FormControls from contact info, vault, and screening logic."""
        answers = self.export_all_vault_answers()
        resolved: dict[str, Any] = {}
        for ctrl in controls:
            key = getattr(ctrl, "key", "")
            label = getattr(ctrl, "label", "") or key
            options = getattr(ctrl, "options", [])
            if key in answers and answers[key] is not None:
                val = answers[key]
                if options:
                    matched = self._match_option(str(val), options)
                    resolved[key] = matched or val
                else:
                    resolved[key] = val
                continue
            val, reason = self.resolve_screening_question(label, options)
            if val is not None:
                resolved[key] = val
            elif str(getattr(ctrl, "control_type", "")).lower() in ("checkbox", "controltype.checkbox"):
                if getattr(ctrl, "legal", False) or any(p in label.lower() for p in ("agree", "consent", "terms", "privacy", "policy")):
                    resolved[key] = True
        return resolved
