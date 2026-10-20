"""LLM provider backed by a local Ollama server (free, no API key required)."""
from __future__ import annotations

import httpx

from app.services.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "").strip()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Ollama request failed ({exc}). Is Ollama running at {self.base_url}? "
                "Install it from https://ollama.com and run `ollama pull "
                f"{self.model}` first."
            ) from exc
