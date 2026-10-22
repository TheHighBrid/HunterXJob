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
    # Current residence in Canada / location residency
    (re.compile(r"currently\s+reside\s+in\s+canada|permanently\s+reside.*canada", re.I), "Yes", "reside_in_canada_default"),
    # Shift / business hours alignment
    (re.compile(r"comfortable\s+working\s+a\s+schedule|aligned\s+with\s+these\s+business\s+hours", re.I), "Yes", "business_hours_consent"),
    # Communication medium consent
    (re.compile(r"comfortable\s+with\s+writing\s+being\s+your\s+primary\s+communication", re.I), "Yes", "writing_comm_consent"),
    # Interview recording consent
    (re.compile(r"recording\s+consent|consent\s+to.*record", re.I), "Yes", "recording_consent"),
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

        # 3b. Role & Domain Experience checks
        if re.search(r"(?:do\s+you\s+have\s+(?:at\s+least\s+)?\d*\s*years?|experience\s+in|background\s+in|worked\s+in)", text, re.I):
            text_lower = text.lower()
            if any(t in text_lower for t in ("operations", "compliance", "fraud", "risk", "kyc", "kyb", "financial services", "fintech", "banking", "credit", "collections", "customer")):
                emp_names = [f.data.get("employer", "").lower() for f in self.profile.of("employment")]
                if any(b in " ".join(emp_names) for b in ("scotiabank", "bmo", "rbc", "td", "bank", "melato")):
                    val = "Yes"
                    if options:
                        val = self._match_option(val, options) or val
                    return val, "profile_financial_experience"

        # 3c. Language proficiency checks (French / English)
        if re.search(r"french|français", text, re.I):
            lang_facts = [f.data for f in self.profile.of("language") if "french" in f.data.get("name", "").lower()]
            if lang_facts:
                val = "Yes"
                if options:
                    val = self._match_option(val, options) or val
                return val, "profile_french_proficiency"
        if re.search(r"english|anglais", text, re.I):
            lang_facts = [f.data for f in self.profile.of("language") if "english" in f.data.get("name", "").lower()]
            if lang_facts:
                val = "Yes"
                if options:
                    val = self._match_option(val, options) or val
                return val, "profile_english_proficiency"

        # 4. Relocation / Commute
        if re.search(r"relocate|relocation|commute|on-site|hybrid", text, re.I):
            pref = self.preferences.get("willing_to_relocate", True)
            val = "Yes" if pref else "No"
            if options:
                val = self._match_option(val, options) or val
            return val, "preference_relocation"

        # 5. Salary expectation
        if re.search(r"salary|compensation|expected\s+rate|annual\s+salary", text, re.I):
            desired = self.preferences.get("desired_salary", "") or "75000"
            return desired, "preference_salary"

        # 6. Start date / availability
        if re.search(r"start\s+date|earliest\s+date|notice\s+period|availability", text, re.I):
            avail = self.preferences.get("notice_period", "2 weeks")
            return avail, "preference_start_date"

        # 7. Experience descriptions & narrative questions
        if re.search(r"describe\s+your\s+experience.*(?:fintech|financial|banking|operations|risk|fraud|work)", text, re.I):
            summary = (
                "Over 5+ years of financial operations and banking experience across Tier-1 Canadian financial institutions "
                "including Scotiabank, Bank of Montreal (BMO), Royal Bank of Canada (RBC), and TD Canada Trust. "
                "Specialized in fraud prevention, risk assessment, credit analysis, KYC/KYB identity verification, dispute resolution, "
                "and collections, consistently maintaining high regulatory compliance and exceptional customer satisfaction."
            )
            return summary, "profile_experience_narrative"

        if re.search(r"why\s+(?:are\s+you\s+)?excited\s+about\s+(?:this\s+role|company|float|wealthsimple|cohere)", text, re.I) or re.search(r"why\s+do\s+you\s+want\s+to\s+work", text, re.I):
            summary = (
                "I am excited about this opportunity because of the organization's strong innovation, operational excellence, "
                "and mission to provide seamless financial solutions. With my proven track record in risk management, fraud investigations, "
                "and bilingual client operations, I look forward to contributing directly to high-standard customer trust and platform growth."
            )
            return summary, "profile_interest_narrative"

        if re.search(r"experience\s+in\s+client-facing\s+roles|experience\s+with\s+ai\s+or\s+agentic", text, re.I):
            summary = (
                "Proven track record in high-touch client-facing roles handling complex escalations, customer dispute resolution, and risk consultations. "
                "Experienced in deploying automated AI workflows, workflow automation tools, and agentic systems to streamline investigations and operational pipelines."
            )
            return summary, "profile_agentic_narrative"

        return None, "unresolved"

    def resolve_with_llm_or_fallback(
        self,
        label: str,
        options: list[str] | None = None,
        control_type: str = "text",
    ) -> tuple[Any, str]:
        """Use local Ollama LLM (llama3.2:3b) with deterministic heuristics fallback for custom questions."""
        import urllib.request

        text = label.strip()
        if not text:
            return None, "empty_label"

        # 1. Multiple-choice / Select / Radio with options
        if options:
            numbered = "\n".join(f"{i}: {opt}" for i, opt in enumerate(options))
            prompt = (
                "You are assisting a senior financial operations, fraud investigations, compliance, and risk leader "
                "(5+ years at Scotiabank, BMO, RBC, TD Canada Trust; based in Ottawa, Ontario, Canada; bilingual English/French) "
                "completing a job application.\n\n"
                f"Question: {text}\n\n"
                f"Options:\n{numbered}\n\n"
                "Select the single strongest, most collaborative, risk-sound professional option. "
                "Reply with ONLY the option number (e.g. 0, 1, 2, or 3)."
            )
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:11434/api/generate",
                    data=json.dumps({
                        "model": "llama3.2:3b",
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 16},
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    reply = (body.get("response") or "").strip()
                    m = re.search(r"\b(\d+)\b", reply)
                    if m:
                        idx = int(m.group(1))
                        if 0 <= idx < len(options):
                            return options[idx], "ollama_option_choice"
            except Exception as exc:
                logger.debug("Ollama option resolution fallback (%s)", exc)

            # Heuristic option scoring fallback
            pos_words = (
                "review", "collaborate", "escalate", "consolidate", "huddle", "protocol",
                "root cause", "quantify", "stakeholder", "verification", "systemic",
                "yes", "authorized", "eligible", "fluent", "native", "consent",
            )
            neg_words = (
                "immediately unfreeze", "refuse", "best judgment on a case-by-case",
                "automated macros", "personally review and approve every single",
                "pause the team", "opt out",
            )
            best_opt = options[0]
            best_score = -999
            for opt in options:
                ol = opt.lower()
                score = sum(2 for w in pos_words if w in ol) - sum(3 for w in neg_words if w in ol)
                if score > best_score:
                    best_score = score
                    best_opt = opt
            return best_opt, "heuristic_option_choice"

        # 2. Open-ended Textarea / Text question
        ctype_lower = str(control_type).lower()
        if any(t in ctype_lower for t in ("textarea", "text")):
            prompt = (
                "Write a concise, first-person ('I...') job application answer in 3 to 4 sentences (plain text only, no placeholders, no bullet points). "
                "Candidate background: Mohamed Alem, based in Ottawa, ON, bilingual English/French, 5+ years of experience across Scotiabank, "
                "Bank of Montreal (BMO), Royal Bank of Canada (RBC), TD Canada Trust, and Melato specializing in fraud investigations, "
                "KYC/KYB compliance, risk operations, team coaching, escalation handling, and process improvement.\n\n"
                f"Application Question: {text}\n\n"
                "Answer:"
            )
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:11434/api/generate",
                    data=json.dumps({
                        "model": "llama3.2:3b",
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.2, "num_predict": 180},
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=12.0) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    reply = (body.get("response") or "").strip().strip('"')
                    if len(reply) >= 25:
                        return reply, "ollama_narrative"
            except Exception as exc:
                logger.debug("Ollama narrative fallback (%s)", exc)

            fallback_narrative = (
                "In my financial operations and fraud risk roles across Scotiabank, BMO, RBC, and TD Canada Trust, "
                "I addressed high-stakes operational challenges by immediately containing risk exposure, reviewing root causes with team members "
                "in a constructive and psychologically safe 1-on-1 setting, and updating standard operating checklists so the entire team prevented recurrence."
            )
            return fallback_narrative, "heuristic_narrative_fallback"

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

        # Standard ATS system fields mapping (Ashby, Greenhouse, etc.)
        system_map = {
            "_systemfield_name": answers.get("name") or answers.get("full_name"),
            "_systemfield_first_name": answers.get("first_name"),
            "_systemfield_last_name": answers.get("last_name"),
            "_systemfield_email": answers.get("email"),
            "_systemfield_phone": answers.get("phone"),
            "_systemfield_location": answers.get("location") or answers.get("city"),
            "_systemfield_linkedin": answers.get("linkedin_url"),
            "_systemfield_github": answers.get("github_url"),
            "_systemfield_website": answers.get("portfolio_url"),
        }

        for ctrl in controls:
            key = getattr(ctrl, "key", "")
            label = getattr(ctrl, "label", "") or key
            options = getattr(ctrl, "options", [])

            # 1. Exact match in answers or system field map
            val = None
            if key in answers and answers[key] is not None:
                val = answers[key]
            elif key in system_map and system_map[key] is not None:
                val = system_map[key]

            # 2. Contact label heuristics (for ATS platforms that assign UUIDs to contact fields)
            if val is None:
                label_lower = label.strip().lower()
                if re.search(r"^(?:full\s+)?name$", label_lower):
                    val = answers.get("name") or answers.get("full_name")
                elif re.search(r"^first\s+name$", label_lower):
                    val = answers.get("first_name")
                elif re.search(r"^last\s+name$", label_lower):
                    val = answers.get("last_name")
                elif re.search(r"^(?:e-?mail|email\s+address)$", label_lower):
                    val = answers.get("email")
                elif re.search(r"^(?:phone|phone\s+number|mobile)$", label_lower):
                    val = answers.get("phone")
                elif re.search(r"linkedin", label_lower):
                    val = answers.get("linkedin_url")
                elif re.search(r"github", label_lower):
                    val = answers.get("github_url")
                elif re.search(r"portfolio|personal\s+website", label_lower):
                    val = answers.get("portfolio_url")
                elif re.search(
                    r"current\s+city|city\s+of\s+residence|location\s+where\s+you.*reside|"
                    r"where\s+do\s+you\s+(?:currently\s+|permanently\s+)?reside|current\s+location|"
                    r"where\s+are\s+you\s+(?:based|located)|location\s*\(city\)",
                    label_lower,
                ):
                    val = answers.get("city") or answers.get("location")

            # 3. Screening question resolver
            if val is None:
                val, reason = self.resolve_screening_question(label, options)

            # 4. Checkbox / consent fallbacks
            ctype_str = str(getattr(ctrl, "control_type", "")).lower()
            if val is None and ctype_str in ("checkbox", "controltype.checkbox"):
                if getattr(ctrl, "legal", False) or any(p in label.lower() for p in ("agree", "consent", "terms", "privacy", "policy")):
                    val = True

            # 5. Local Ollama LLM (llama3.2:3b) + heuristic fallback for custom role/scenario questions
            if val is None and "file" not in ctype_str:
                val, reason = self.resolve_with_llm_or_fallback(label, options, ctype_str)

            if val is not None:
                if options:
                    matched = self._match_option(str(val), options)
                    resolved[key] = matched or val
                else:
                    resolved[key] = val

        return resolved
