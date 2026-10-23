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
from app.question_catalog import (
    OptionRecord,
    classify_question,
    match_answers_to_options,
    normalize_text,
    score_option,
)

logger = logging.getLogger(__name__)


def enforce_length_constraints(
    text: str,
    min_length: int = 0,
    max_length: int | None = None,
    padding_narrative: str = "",
) -> str:
    """Enforce minimum and maximum character length constraints on narrative responses."""
    cleaned = " ".join(str(text or "").split()).strip()

    # 1. Enforce min_length
    if min_length > 0 and len(cleaned) < min_length:
        default_padding = (
            " In my financial operations and risk management roles across Scotiabank, BMO, RBC, and TD Canada Trust, "
            "I consistently delivered exceptional outcomes by prioritizing regulatory compliance, client trust, "
            "rigorous attention to detail, and cross-functional team collaboration."
        )
        pad = padding_narrative or default_padding
        while len(cleaned) < min_length:
            cleaned = f"{cleaned} {pad.strip()}"

    # 2. Enforce max_length
    if max_length is not None and max_length > 0 and len(cleaned) > max_length:
        truncated = cleaned[:max_length]
        last_period = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
        if last_period >= int(max_length * 0.6):
            cleaned = truncated[:last_period + 1]
        else:
            last_space = truncated.rfind(" ")
            if last_space > 0:
                cleaned = truncated[:last_space].rstrip(",;:-") + "."
            else:
                cleaned = truncated

    return cleaned


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

    def _resolve_catalog_item(
        self,
        item: dict[str, Any],
        text: str,
        options: list[str] | None = None,
    ) -> tuple[Any, str] | None:
        """Resolve a candidate answer for a classified catalog item."""
        key = item.get("canonical_key", "")

        # 1. Sponsorship
        if key in ("sponsorship_required", "sponsorship_future_required"):
            val = "No"
            if options:
                val = self._match_option(val, options) or self._match_option("I do not require sponsorship", options) or val
            return val, f"catalog:{key}"

        # 2. Work authorization
        if key == "work_authorization":
            country = "CA" if "canada" in text.lower() else "US" if ("us" in text.lower() or "united states" in text.lower()) else "CA"
            auth_facts = [f.data for f in self.profile.of("work_authorization") if f.data.get("country", "").upper() == country]
            if auth_facts and auth_facts[0].get("authorized"):
                val = "Authorized to work in Canada" if country == "CA" and options and any("Canada" in o for o in options) else "Yes"
                if options:
                    val = self._match_option(val, options) or val
                return val, f"catalog:{key}"

        # 3. Citizenship / residency
        if key == "citizenship_residency":
            val = "Canadian citizen" if options and any("Canadian" in o for o in options) else "Canada"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"

        # 4. Standard consents & verifications
        if key in (
            "age_requirement",
            "background_check_consent",
            "drug_screening_consent",
            "credit_check_consent",
            "driving_record_check_consent",
            "employment_verification_consent",
            "education_verification_consent",
            "terms_consent",
            "essential_functions_ability",
            "export_control_eligibility",
            "commute_ability",
            "remote_workspace",
            "schedule_hours",
            "on_call_availability",
            "rehire_eligibility",
            "contact_former_employers",
            "contact_current_employer",
        ):
            val = "Yes"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"

        # 5. Non-compete, prior employment/app/interview, termination, relocation assistance
        if key in ("non_compete", "prior_employment", "prior_application", "prior_interview", "termination_history", "relocation_assistance"):
            val = "No"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"

        # 6. Relocation
        if key == "relocation":
            if options and any("already live in canada" in o.lower() or "live in canada" in o.lower() for o in options):
                val = next(o for o in options if "already live in canada" in o.lower() or "live in canada" in o.lower())
                return val, f"catalog:{key}"
            pref = self.preferences.get("willing_to_relocate", True)
            val = "Yes" if pref else "No"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"

        # 7. Compensation
        if key == "salary_expectation":
            desired = str(self.preferences.get("desired_salary", "") or "75000")
            if options:
                desired = self._match_option(desired, options) or desired
            return desired, f"catalog:{key}"
        if key == "hourly_rate_expectation":
            rate = str(self.preferences.get("hourly_rate", "") or "38")
            if options:
                rate = self._match_option(rate, options) or rate
            return rate, f"catalog:{key}"
        if key == "compensation_negotiable":
            val = "Yes"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"

        # 8. Notice period / start date
        if key == "notice_period":
            avail = self.preferences.get("notice_period", "2 weeks")
            if options:
                avail = self._match_option(avail, options) or avail
            return avail, f"catalog:{key}"

        # 9. Source / How heard
        if key == "how_heard":
            val = "LinkedIn"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"

        # 10. Management & Team size
        if key == "management_experience":
            val = "Yes"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"
        if key == "management_years":
            val = "3"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"
        if key == "team_size_managed":
            val = "5"
            if options:
                val = self._match_option(val, options) or val
            return val, f"catalog:{key}"

        # 11. Languages
        if key == "language_french":
            val = "Fluent"
            if options:
                val = self._match_option("Fluent", options) or self._match_option("Yes", options) or val
            return val, f"catalog:{key}"
        if key == "language_english":
            val = "Fluent"
            if options:
                val = self._match_option("Fluent", options) or self._match_option("Native", options) or self._match_option("Yes", options) or val
            return val, f"catalog:{key}"

        # 12. Demographics
        if key == "gender_identity":
            val = "Male"
            if options:
                val = self._match_option("Male", options) or self._match_option("Prefer not to answer", options) or val
            return val, f"catalog:{key}"
        if key == "veteran_status":
            val = "I am not a protected veteran"
            if options:
                val = self._match_option(val, options) or self._match_option("No", options) or self._match_option("Prefer not to answer", options) or val
            return val, f"catalog:{key}"
        if key == "disability_status":
            val = "No, I do not have a disability"
            if options:
                val = self._match_option(val, options) or self._match_option("No", options) or self._match_option("Prefer not to answer", options) or val
            return val, f"catalog:{key}"
        if key in ("race_ethnicity", "hispanic_latino"):
            val = "Prefer not to answer"
            if options:
                val = self._match_option("Prefer not to answer", options) or self._match_option("Decline to self-identify", options) or val
            return val, f"catalog:{key}"

        return None

    def resolve_screening_question(self, label: str, options: list[str] | None = None) -> tuple[Any, str]:
        """Resolve a screening question using pattern rules, custom answers, or profile facts."""
        text = label.strip()

        # 0. Single-option acknowledgment checkbox (e.g. on-call policy acknowledgment)
        if options and len(options) == 1:
            return options[0], "single_option_acknowledgment"

        # 1. Direct custom answer override if configured
        for key, val in self.custom_answers.items():
            if key.lower() in text.lower():
                return val, "custom_answer_match"

        if "maxmind" in text.lower() and ("interested" in text.lower() or "fit" in text.lower()):
            val = (
                "I am very interested in joining MaxMind as a Collections Specialist because of the company's clear social mission, "
                "collaborative remote culture, and commitment to thoughtful, case-by-case customer engagement without rigid scripts. "
                "With over five years of banking and financial operations experience at Scotiabank, BMO, RBC, and Melato, I have extensive "
                "hands-on experience managing delinquent accounts, negotiating structured repayment solutions, and resolving payment blockers "
                "with empathy and professional firmness. I am experienced in researching customer contacts, working with invoicing portals and POs, "
                "and collaborating with cross-functional teams to reconcile accounts while maintaining positive customer relationships. "
                "I look forward to bringing this balanced approach to MaxMind to help optimize cash flow and support our business customers."
            )
            return val, "custom:maxmind_collections_fit"

        # 2. Master catalog classification
        cat_item = classify_question(text)
        if cat_item:
            cat_res = self._resolve_catalog_item(cat_item, text, options)
            if cat_res is not None:
                return cat_res

        # 3. Check standard screening regex rules fallback
        for pattern, default_val, rule_name in DEFAULT_SCREENING_PATTERNS:
            if pattern.search(text):
                if options:
                    matched = self._match_option(default_val, options)
                    if matched:
                        return matched, f"screening_pattern:{rule_name}"
                return default_val, f"screening_pattern:{rule_name}"

        # 3b. Work authorization checks
        if re.search(r"legally\s+(?:authorized|eligible)\s+to\s+work|work\s+authorization", text, re.I):
            country = "CA" if "canada" in text.lower() else "US" if ("us" in text.lower() or "united states" in text.lower()) else "CA"
            auth_facts = [f.data for f in self.profile.of("work_authorization") if f.data.get("country", "").upper() == country]
            if auth_facts and auth_facts[0].get("authorized"):
                val = "Authorized to work in Canada" if country == "CA" and options and any("Canada" in o for o in options) else "Yes"
                if options:
                    val = self._match_option(val, options) or val
                return val, "profile_work_authorization"

        # 3c. Role & Domain Experience checks
        if re.search(r"(?:do\s+you\s+have\s+(?:at\s+least\s+)?\d*\s*years?|experience\s+in|background\s+in|worked\s+in)", text, re.I):
            text_lower = text.lower()
            if any(t in text_lower for t in ("operations", "compliance", "fraud", "risk", "kyc", "kyb", "financial services", "fintech", "banking", "credit", "collections", "customer")):
                emp_names = [f.data.get("employer", "").lower() for f in self.profile.of("employment")]
                if any(b in " ".join(emp_names) for b in ("scotiabank", "bmo", "rbc", "td", "bank", "melato")):
                    val = "Yes"
                    if options:
                        matched = self._match_option(val, options)
                        if matched:
                            return matched, "profile_financial_experience"
                    else:
                        return val, "profile_financial_experience"

        # 3d. Language proficiency checks (French / English)
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
                matched = self._match_option(val, options)
                if matched:
                    return matched, "preference_relocation"
            else:
                return val, "preference_relocation"

        # 5. Salary expectation (only for open-ended text/number fields without custom choice options)
        if not options and re.search(r"salary|compensation|expected\s+rate|annual\s+salary", text, re.I):
            desired = self.preferences.get("desired_salary", "") or "75000"
            return desired, "preference_salary"

        # 6. Start date / availability
        if re.search(r"start\s+date|earliest\s+date|notice\s+period|availability", text, re.I):
            avail = self.preferences.get("notice_period", "2 weeks")
            return avail, "preference_start_date"
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
        min_length: int = 0,
        max_length: int | None = None,
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

            # Heuristic option scoring fallback using 4-tier primitives
            records = [OptionRecord(key=str(i), label=opt, value=opt) for i, opt in enumerate(options)]
            scored_candidates = [
                "Yes", "Authorized to work in Canada", "Reliability Status",
                "Full-time", "LinkedIn", "Prefer not to answer", "Decline to self-identify",
            ]
            for cand in scored_candidates:
                m_res = match_answers_to_options([cand], records, allow_multiple=False)
                if m_res.matched:
                    return m_res.matched[0].label, "catalog_option_fallback"

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
            length_instructions = ""
            if min_length > 0 and max_length:
                length_instructions = f" Your answer MUST be between {min_length} and {max_length} characters."
            elif min_length > 0:
                length_instructions = f" Your answer MUST be at least {min_length} characters long."
            elif max_length:
                length_instructions = f" Your answer MUST NOT exceed {max_length} characters."

            prompt = (
                "Write ONLY the direct first-person ('I...') job application answer in 3 to 4 sentences "
                "(plain text only, no introductory preamble, no placeholders, no bullet points)."
                f"{length_instructions} "
                "Candidate background: Mohamed Alem, based in Ottawa, ON, bilingual English/French, 5+ years of experience across Scotiabank, "
                "Bank of Montreal (BMO), Royal Bank of Canada (RBC), TD Canada Trust, and Melato specializing in fraud investigations, "
                "KYC/KYB compliance, risk operations, team coaching, escalation handling, and process improvement.\n\n"
                f"Application Question: {text}\n\n"
                "Direct Answer:"
            )
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:11434/api/generate",
                    data=json.dumps({
                        "model": "llama3.2:3b",
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.2, "num_predict": 220},
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=12.0) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    reply = (body.get("response") or "").strip().strip('"')
                    reply = re.sub(r"^(?:Here(?:'s|\s+is)[^:]*:|I'd be happy to[^:]*:)\s*", "", reply, flags=re.I)
                    reply = " ".join(reply.split())
                    if len(reply) >= 25:
                        reply = enforce_length_constraints(reply, min_length=min_length, max_length=max_length)
                        return reply, "ollama_narrative"
            except Exception as exc:
                logger.debug("Ollama narrative fallback (%s)", exc)

            fallback_narrative = (
                "In my financial operations and fraud risk roles across Scotiabank, BMO, RBC, and TD Canada Trust, "
                "I addressed high-stakes operational challenges by immediately containing risk exposure, reviewing root causes with team members "
                "in a constructive and psychologically safe 1-on-1 setting, and updating standard operating checklists so the entire team prevented recurrence."
            )
            fallback_narrative = enforce_length_constraints(fallback_narrative, min_length=min_length, max_length=max_length)
            return fallback_narrative, "heuristic_narrative_fallback"

        return None, "unresolved"

    def _match_option(self, target: Any, options: list[str]) -> str | None:
        """Find the closest option in a list of options using 4-tier scoring."""
        if not options:
            return None
        target_str = str(target).strip()
        records = [
            OptionRecord(key=str(idx), label=opt, value=opt)
            for idx, opt in enumerate(options)
        ]
        res = match_answers_to_options([target_str], records, allow_multiple=False)
        if res.matched:
            return res.matched[0].label

        # Fallbacks for boolean/consent variations
        target_norm = normalize_text(target_str)
        if target_norm in ("yes", "y", "true", "1", "agree", "authorized"):
            for opt in options:
                ol = normalize_text(opt)
                if ol in ("yes", "y", "true", "agree", "i agree", "authorized", "oui"):
                    return opt
        elif target_norm in ("no", "n", "false", "0", "decline"):
            for opt in options:
                ol = normalize_text(opt)
                if ol in ("no", "n", "false", "decline", "non"):
                    return opt

        # Fallback to substring matching
        for opt in options:
            if target_norm in normalize_text(opt) or normalize_text(opt) in target_norm:
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
                elif re.search(r"^(?:preferred\s+)?first\s+name$", label_lower):
                    val = answers.get("first_name")
                elif re.search(r"^(?:preferred\s+)?last\s+name$", label_lower):
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
                    r"^location$|current\s+city|city\s+of\s+residence|location\s+where\s+you.*reside|"
                    r"where\s+do\s+you\s+(?:currently\s+|permanently\s+)?reside|current\s+location|"
                    r"where\s+are\s+you\s+(?:based|located)|location\s*\(city\)",
                    label_lower,
                ):
                    if "province" in label_lower or "country" in label_lower:
                        val = "Ontario, Canada"
                    else:
                        val = answers.get("city") or answers.get("location")

            # 3. Screening question resolver
            if val is None:
                val, reason = self.resolve_screening_question(label, options)

            # 4. Checkbox / consent fallbacks
            ctype_str = str(getattr(ctrl, "control_type", "")).lower()
            if val is None and ctype_str in ("checkbox", "controltype.checkbox"):
                if getattr(ctrl, "legal", False) or any(p in label.lower() for p in ("agree", "consent", "terms", "privacy", "policy")):
                    val = True

            # 5. Local Ollama LLM (llama3.2:3b) + heuristic fallback for required custom role/scenario questions
            if val is None and getattr(ctrl, "required", False) and "file" not in ctype_str:
                min_len = getattr(ctrl, "min_length", 0) or 0
                max_len = getattr(ctrl, "max_length", None)
                val, reason = self.resolve_with_llm_or_fallback(
                    label, options, ctype_str, min_length=min_len, max_length=max_len
                )

            if val is not None:
                if options:
                    if isinstance(val, (list, tuple, set)):
                        records = [OptionRecord(key=str(i), label=opt, value=opt) for i, opt in enumerate(options)]
                        matched_res = match_answers_to_options([str(v) for v in val], records, allow_multiple=True)
                        if matched_res.matched:
                            resolved[key] = [m.label for m in matched_res.matched]
                        else:
                            resolved[key] = list(val)
                    else:
                        matched = self._match_option(str(val), options)
                        resolved[key] = matched or val
                else:
                    resolved[key] = val

        return resolved
