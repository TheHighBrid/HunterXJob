"""Generic OpenAI chat-completions-compatible LLM provider.

Works against OpenAI itself, or any compatible endpoint (Groq, OpenRouter,
LM Studio, vLLM's OpenAI-compat server, etc.) by pointing base_url/api_key
at it.
"""
from __future__ import annotations

import httpx

from app.services.llm.base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"OpenAI-compatible LLM request to {self.base_url} failed: {exc}"
            ) from exc
