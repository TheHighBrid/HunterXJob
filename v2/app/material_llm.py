"""Optional LLM rewording of generated materials (off by default).

The LLM may only *rephrase* text that already came from verified facts:
résumé bullets, the summary and cover-letter paragraphs. It never sees or
changes employers, titles, dates, degrees or the list of skills, and its
output must pass the truthfulness guard. On any problem (disabled, network
error, bad JSON, extra/missing items, a guard violation) the deterministic
text is kept and the reason is recorded.

Providers (env): MATERIALS_LLM_PROVIDER=ollama (uses OLLAMA_BASE_URL unless
MATERIALS_LLM_BASE_URL is set) or openai (any OpenAI-compatible
``/v1/chat/completions`` endpoint with MATERIALS_LLM_API_KEY).
"""

from __future__ import annotations

import copy
import json
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.profile import VerifiedProfile
from app.truth_guard import check_cover_letter, check_resume

_RULES = (
    "Rewrite each item for clarity and concision. Hard rules: keep the same meaning; do not add any fact, "
    "number, percentage, date, company, product, tool, technology, skill, title, degree or certification "
    "that is not already in that same item; keep every number exactly as written; do not merge or split items. "
    "Answer with only a JSON object mapping each id to its rewritten text."
)


class LLMClient(Protocol):
    name: str

    def complete(self, prompt: str) -> str: ...


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float) -> None:
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.name = f"ollama:{model}"

    def complete(self, prompt: str) -> str:
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "format": "json",
                  "options": {"temperature": 0.2}},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return str(response.json().get("response", ""))


class OpenAICompatibleClient:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: float) -> None:
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout
        self.name = f"openai:{model}"

    def complete(self, prompt: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={"model": self.model, "temperature": 0.2, "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])


def llm_client(settings: Settings) -> LLMClient | None:
    """The configured rewording client, or None when MATERIALS_LLM_ENABLED is false."""
    if not settings.materials_llm_enabled:
        return None
    timeout = settings.materials_llm_timeout
    if settings.materials_llm_provider == "openai":
        if not (settings.materials_llm_base_url and settings.materials_llm_model):
            return None
        return OpenAICompatibleClient(settings.materials_llm_base_url, settings.materials_llm_model,
                                      settings.materials_llm_api_key, timeout)
    return OllamaClient(settings.materials_llm_base_url or settings.ollama_base_url,
                        settings.materials_llm_model or settings.ollama_quality_model, timeout)


def _ask(client: LLMClient, items: dict[str, str]) -> dict[str, str]:
    prompt = f"{_RULES}\n\nItems:\n{json.dumps(items, ensure_ascii=False, indent=1)}"
    raw = client.complete(prompt)
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else raw)
    if not isinstance(data, dict) or set(data) != set(items):
        raise ValueError("reworded items do not match the requested ids")
    out: dict[str, str] = {}
    for key, original in items.items():
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > len(original) * 2 + 80:
            raise ValueError(f"item {key} is empty or too long")
        out[key] = " ".join(value.split())
    return out


_FAILURES = (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError)


def _resume_items(doc: dict[str, Any]) -> dict[str, str]:
    items = {"summary": doc["summary"]["text"]} if doc.get("summary") else {}
    for section in ("experience", "projects"):
        for entry_index, entry in enumerate(doc[section]):
            for bullet_index, bullet in enumerate(entry["bullets"]):
                items[f"{section}.{entry_index}.{bullet_index}"] = bullet["text"]
    return items


def _apply_resume(doc: dict[str, Any], reworded: dict[str, str]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    for key, text in reworded.items():
        if key == "summary":
            out["summary"]["text"] = text
            continue
        section, entry_index, bullet_index = key.split(".")
        out[section][int(entry_index)]["bullets"][int(bullet_index)]["text"] = text
    return out


def reword_resume(doc: dict[str, Any], profile: VerifiedProfile, client: LLMClient | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (document to use, report). Falls back to ``doc`` unless the guard accepts the rewording."""
    if client is None:
        return doc, {"status": "disabled"}
    items = _resume_items(doc)
    if not items:
        return doc, {"status": "nothing_to_reword"}
    try:
        candidate = _apply_resume(doc, _ask(client, items))
    except _FAILURES as exc:
        return doc, {"status": "error", "llm": client.name, "error": f"{exc.__class__.__name__}: {exc}"[:300]}
    violations = check_resume(candidate, profile)
    if violations:
        return doc, {"status": "rejected", "llm": client.name, "violations": violations[:20]}
    return candidate, {"status": "accepted", "llm": client.name}


def reword_cover_letter(doc: dict[str, Any], profile: VerifiedProfile, client: LLMClient | None,
                        job_description: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    if client is None:
        return doc, {"status": "disabled"}
    items = {f"p{index}": paragraph["text"] for index, paragraph in enumerate(doc["paragraphs"])}
    try:
        reworded = _ask(client, items)
    except _FAILURES as exc:
        return doc, {"status": "error", "llm": client.name, "error": f"{exc.__class__.__name__}: {exc}"[:300]}
    candidate = copy.deepcopy(doc)
    for index, paragraph in enumerate(candidate["paragraphs"]):
        paragraph["text"] = reworded[f"p{index}"]
    violations = check_cover_letter(candidate, profile, job_description=job_description)
    if violations:
        return doc, {"status": "rejected", "llm": client.name, "violations": violations[:20]}
    return candidate, {"status": "accepted", "llm": client.name}
