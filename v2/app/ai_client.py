from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings


class LocalAIError(RuntimeError):
    pass


class LocalAIClient:
    def __init__(self) -> None:
        self.base_url = settings.ai_base_url.rstrip("/")
        self.timeout = httpx.Timeout(
            connect=settings.ai_connect_timeout_seconds,
            read=settings.ai_generation_timeout_seconds,
            write=30.0,
            pool=30.0,
        )

    async def health(self) -> dict[str, Any]:
        endpoint = "/api/tags" if settings.ai_provider == "ollama" else "/health"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}{endpoint}")
            response.raise_for_status()
            return response.json()

    async def generate(self, prompt: str, *, quality: bool = False) -> str:
        model = settings.ai_quality_model if quality else settings.ai_fast_model
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": settings.ai_keep_alive,
            "options": {"temperature": 0.2},
        }
        last_error: Exception | None = None

        for attempt in range(settings.ai_max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(f"{self.base_url}/api/generate", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    text = str(data.get("response", "")).strip()
                    if not text:
                        raise LocalAIError("Local model returned an empty response")
                    return text
            except (httpx.HTTPError, LocalAIError) as exc:
                last_error = exc
                if attempt < settings.ai_max_retries:
                    await asyncio.sleep(2**attempt)

        raise LocalAIError(f"Local AI generation failed after retries: {last_error}")
