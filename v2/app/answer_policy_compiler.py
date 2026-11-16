"""Master Answer Policy Compiler and Runtime Engine.

Compiles, loads, and indexes the comprehensive HunterXJob / JobTomatik
portable Answer Policy (v3.0) containing 140+ question families,
multilingual patterns (EN/FR), canonical answers, variant formats,
option equivalents, decision contracts, and safety guardrails.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.question_catalog import normalize_text

logger = logging.getLogger(__name__)

def _resolve_vault_path(filename: str) -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent / "docs" / "Answers Vault" / filename,
        Path(__file__).resolve().parent.parent.parent / "docs" / "Answers Vault" / filename,
        Path.cwd() / "docs" / "Answers Vault" / filename,
        Path.cwd().parent / "docs" / "Answers Vault" / filename,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]

DEFAULT_POLICY_PATH = _resolve_vault_path("HunterXJob_Master_Answer_Policy_v3.yaml")
DEFAULT_SEED_JSON_PATH = _resolve_vault_path("HunterXJob_Vault_Seed_POST_api_answers.json")


@dataclass(frozen=True)
class PolicyQuestionItem:
    key: str
    stage: int
    category: str
    questions: list[str]
    mode: str  # "auto", "review", "skip", "contextual"
    answer: str
    answer_variants: list[str] = field(default_factory=list)
    option_equivalents: list[str] = field(default_factory=list)
    condition: str | None = None
    sensitive: bool = False
    notes: str = ""
    compiled_patterns: list[re.Pattern[str]] = field(default_factory=list)


@dataclass
class MasterAnswerPolicy:
    schema_version: str
    project: str
    as_of: str
    decision_contract: dict[str, Any]
    targeting: dict[str, Any]
    profile_facts: dict[str, Any]
    items_by_key: dict[str, PolicyQuestionItem]
    items_list: list[PolicyQuestionItem]

    def get(self, key: str) -> PolicyQuestionItem | None:
        return self.items_by_key.get(key)

    def find_match(self, question_text: str) -> tuple[PolicyQuestionItem | None, float]:
        """Find the best matching PolicyQuestionItem for the given question text.

        Returns (matched_item, score).
        """
        normalized = normalize_text(question_text)
        if not normalized:
            return None, 0.0

        best_item: PolicyQuestionItem | None = None
        best_score = 0.0

        for item in self.items_list:
            # 1. Exact regex pattern match
            for pat in item.compiled_patterns:
                if pat.search(question_text) or pat.search(normalized):
                    return item, 100.0

            # 2. Token overlap with item question phrases
            for q in item.questions:
                q_norm = normalize_text(q)
                if q_norm == normalized:
                    return item, 100.0
                if q_norm in normalized or normalized in q_norm:
                    score = 90.0
                    if score > best_score:
                        best_score = score
                        best_item = item

                # Token set intersection
                q_tokens = set(q_norm.split())
                n_tokens = set(normalized.split())
                if q_tokens and n_tokens:
                    inter = q_tokens & n_tokens
                    union = q_tokens | n_tokens
                    jaccard = len(inter) / len(union) if union else 0.0
                    if jaccard >= 0.70 and jaccard * 100 > best_score:
                        best_score = jaccard * 100
                        best_item = item

        if best_score >= 70.0:
            return best_item, best_score
        return None, 0.0


_POLICY_CACHE: MasterAnswerPolicy | None = None


def _compile_pattern_from_phrase(phrase: str) -> re.Pattern[str]:
    """Turn a question phrase into a flexible matching regex."""
    escaped = re.escape(phrase.strip("? .!"))
    # Allow whitespace and punctuation flexibility
    pattern_str = r"\b" + re.sub(r"\\\s+", r"\\s+", escaped) + r"\b"
    return re.compile(pattern_str, re.I)


def load_master_policy(path: Path | str | None = None, *, force_reload: bool = False) -> MasterAnswerPolicy:
    """Load and compile MasterAnswerPolicy from YAML file."""
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not force_reload and path is None:
        return _POLICY_CACHE

    target_path = Path(path) if path else DEFAULT_POLICY_PATH
    if not target_path.is_file():
        target_path = _resolve_vault_path("HunterXJob_Master_Answer_Policy_v3.yaml")
    if not target_path.is_file():
        raise FileNotFoundError(f"Master Answer Policy YAML not found at {target_path}")

    raw = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}

    items_by_key: dict[str, PolicyQuestionItem] = {}
    items_list: list[PolicyQuestionItem] = []

    for entry in raw.get("question_bank", []):
        key = str(entry.get("key") or "").strip()
        if not key:
            continue

        raw_questions = [str(q).strip() for q in entry.get("questions") or [] if str(q).strip()]
        compiled: list[re.Pattern[str]] = []
        for q in raw_questions:
            try:
                compiled.append(_compile_pattern_from_phrase(q))
            except Exception as e:
                logger.debug("Failed compiling pattern for %r: %s", q, e)

        item = PolicyQuestionItem(
            key=key,
            stage=int(entry.get("stage") or 1),
            category=str(entry.get("category") or "general"),
            questions=raw_questions,
            mode=str(entry.get("mode") or "auto").strip().lower(),
            answer=str(entry.get("answer") or "").strip(),
            answer_variants=[str(v).strip() for v in entry.get("answer_variants") or [] if str(v).strip()],
            option_equivalents=[str(o).strip() for o in entry.get("option_equivalents") or [] if str(o).strip()],
            condition=entry.get("condition"),
            sensitive=bool(entry.get("sensitive")),
            notes=str(entry.get("notes") or "").strip(),
            compiled_patterns=compiled,
        )
        items_by_key[key] = item
        items_list.append(item)

    policy = MasterAnswerPolicy(
        schema_version=str(raw.get("schema_version") or "3.0"),
        project=str(raw.get("project") or "HunterXJob"),
        as_of=str(raw.get("as_of") or "2026-10-27"),
        decision_contract=raw.get("decision_contract") or {},
        targeting=raw.get("targeting") or {},
        profile_facts=raw.get("profile_facts") or {},
        items_by_key=items_by_key,
        items_list=items_list,
    )

    if path is None:
        _POLICY_CACHE = policy

    logger.info("Compiled MasterAnswerPolicy v%s with %d question items", policy.schema_version, len(items_list))
    return policy


def get_seed_records(json_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Load user seed records from JSON file."""
    import json

    target = Path(json_path) if json_path else DEFAULT_SEED_JSON_PATH
    if not target.is_file():
        target = _resolve_vault_path("HunterXJob_Vault_Seed_POST_api_answers.json")
    if not target.is_file():
        return []

    data = json.loads(target.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []
